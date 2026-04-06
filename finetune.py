"""
finetune.py — QLoRA fine-tuning Qwen 2.5 7B на данных агента.

Запуск на Vast.ai (2× RTX 4090):
  pip install torch transformers peft trl datasets bitsandbytes accelerate
  python finetune.py

Результат: ./agent-qwen-7b-lora/ (LoRA-адаптер)

Для merge в полную модель:
  python finetune.py --merge
"""

import argparse
import os
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer

# ── Конфигурация ──────────────────────────────────────────────────────────────

MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
OUTPUT_DIR = "./agent-qwen-7b-lora"
MERGED_DIR = "./agent-qwen-7b-merged"

# Гиперпараметры (оптимизированы для 218 примеров + 2×4090)
EPOCHS = 4
BATCH_SIZE = 2
GRAD_ACCUM = 4       # effective batch = 2 * 4 = 8
LR = 2e-4
MAX_SEQ_LEN = 2048
WARMUP_RATIO = 0.1

# LoRA параметры
LORA_R = 64
LORA_ALPHA = 128
LORA_DROPOUT = 0.05
LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", action="store_true",
                        help="Объединить LoRA с базовой моделью")
    parser.add_argument("--model", default=MODEL_ID,
                        help=f"HuggingFace model ID (default: {MODEL_ID})")
    parser.add_argument("--data", default="training_data",
                        help="Директория с train.jsonl и val.jsonl")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--lr", type=float, default=LR)
    args = parser.parse_args()

    if args.merge:
        merge_model(args.model)
        return

    train(args)


def train(args):
    print(f"{'='*60}")
    print(f"Fine-tuning: {args.model}")
    print(f"Data: {args.data}/")
    print(f"Epochs: {args.epochs}, LR: {args.lr}")
    print(f"LoRA: r={LORA_R}, alpha={LORA_ALPHA}")
    print(f"GPU: {torch.cuda.device_count()}× {torch.cuda.get_device_name(0)}")
    print(f"{'='*60}")

    # ── Загрузка данных ──
    train_file = os.path.join(args.data, "train.jsonl")
    # Если есть merged — используем его (после generate_synthetic.py --merge-existing)
    merged_file = os.path.join(args.data, "merged_train.jsonl")
    if os.path.exists(merged_file):
        train_file = merged_file
        print(f"Найден merged датасет: {merged_file}")

    dataset = load_dataset("json", data_files={
        "train": train_file,
        "validation": os.path.join(args.data, "val.jsonl"),
    })
    print(f"Train: {len(dataset['train'])} примеров")
    print(f"Val: {len(dataset['validation'])} примеров")

    # ── Токенизатор ──
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # ── Квантизация 4-bit ──
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    # ── Модель ──
    print("Загрузка модели (4-bit)...")
    try:
        import flash_attn  # noqa: F401
        attn_impl = "flash_attention_2"
        print("  Attention: flash_attention_2")
    except ImportError:
        attn_impl = "sdpa"
        print("  Attention: sdpa (flash-attn не найден)")

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
        attn_implementation=attn_impl,
    )
    model = prepare_model_for_kbit_training(model)

    # ── LoRA ──
    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=LORA_TARGET_MODULES,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    trainable, total = model.get_nb_trainable_parameters()
    print(f"Параметры: {total:,} всего, {trainable:,} обучаемых "
          f"({100*trainable/total:.2f}%)")

    # ── Формирование промптов (ChatML) ──
    def format_chat(example):
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        # Truncate to max_seq_length tokens
        tokens = tokenizer.encode(text, truncation=True, max_length=MAX_SEQ_LEN)
        text = tokenizer.decode(tokens, skip_special_tokens=False)
        return {"text": text}

    dataset = dataset.map(format_chat, remove_columns=["messages"])

    # ── Training ──
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=10,
        weight_decay=0.01,
        bf16=True,
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        dataloader_num_workers=2,
        report_to="none",
        seed=42,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        processing_class=tokenizer,
    )

    print("\nЗапуск обучения...")
    result = trainer.train()

    # ── Сохранение ──
    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    print(f"\n{'='*60}")
    print("Обучение завершено!")
    print(f"  Loss: {result.training_loss:.4f}")
    print(f"  Время: {result.metrics['train_runtime']:.0f}с")
    print(f"  Адаптер: {OUTPUT_DIR}/")
    print("  Для merge: python finetune.py --merge")
    print(f"{'='*60}")


def merge_model(model_id):
    """Объединяет LoRA-адаптер с базовой моделью."""
    from peft import PeftModel

    print(f"Merge: {model_id} + {OUTPUT_DIR} → {MERGED_DIR}")

    print("Загрузка базовой модели (bf16)...")
    base = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )

    print("Применение LoRA...")
    model = PeftModel.from_pretrained(base, OUTPUT_DIR)
    model = model.merge_and_unload()

    print("Сохранение...")
    model.save_pretrained(MERGED_DIR)

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    tokenizer.save_pretrained(MERGED_DIR)

    print(f"Готово: {MERGED_DIR}/")
    print("Можно загрузить: AutoModelForCausalLM.from_pretrained('{MERGED_DIR}')")


if __name__ == "__main__":
    main()
