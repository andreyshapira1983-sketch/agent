"""Tests for core.bare_metal — полный автономный мозг на чистом PyTorch."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

torch = pytest.importorskip('torch')

from core.bare_metal.config import BrainConfig  # noqa: E402
from core.bare_metal.kv_cache import KVCache  # noqa: E402
from core.bare_metal.tokenizer import BPETokenizer, _apply_merge  # noqa: E402
from core.bare_metal.model import (  # noqa: E402
    BareMetalTransformer, RMSNorm, precompute_rope, apply_rotary,
)
from core.bare_metal.inference import InferenceEngine  # noqa: E402
from core.bare_metal.memory import MemoryManager, MemoryEntry  # noqa: E402
from core.bare_metal.action_loop import ActionLoop, parse_structured_output  # noqa: E402
from core.bare_metal.brain import BareMetalBrain  # noqa: E402
from core.bare_metal.training import Trainer  # noqa: E402


# ── Маленький конфиг для быстрых тестов ───────────────────────────────────

def _tiny_cfg() -> BrainConfig:
    return BrainConfig(
        vocab_size=512,
        dim=64,
        n_layers=2,
        n_heads=4,
        n_kv_heads=2,
        max_seq_len=128,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Config
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_defaults(self):
        c = BrainConfig()
        assert c.dim == 384
        assert c.head_dim == 48

    def test_ffn_hidden_alignment(self):
        c = BrainConfig(dim=64)
        assert c.ffn_hidden % 64 == 0

    def test_save_load(self, tmp_path):
        c = BrainConfig(dim=128, n_layers=3)
        path = str(tmp_path / 'cfg.json')
        c.save(path)
        c2 = BrainConfig.load(path)
        assert c2.dim == 128
        assert c2.n_layers == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 2. KV Cache
# ═══════════════════════════════════════════════════════════════════════════════

class TestKVCache:
    def test_update_shapes(self):
        cache = KVCache(batch=1, max_len=32, n_kv_heads=2, head_dim=16)
        k = torch.randn(1, 5, 2, 16)
        v = torch.randn(1, 5, 2, 16)
        k_out, _ = cache.update(k, v, start_pos=0)
        assert k_out.shape == (1, 5, 2, 16)

    def test_incremental(self):
        cache = KVCache(batch=1, max_len=32, n_kv_heads=2, head_dim=8)
        k1 = torch.ones(1, 3, 2, 8)
        v1 = torch.ones(1, 3, 2, 8) * 2
        cache.update(k1, v1, 0)

        k2 = torch.ones(1, 1, 2, 8) * 3
        v2 = torch.ones(1, 1, 2, 8) * 4
        k_out, _ = cache.update(k2, v2, 3)
        assert k_out.shape == (1, 4, 2, 8)
        assert float(k_out[0, 3, 0, 0]) == 3.0

    def test_reset(self):
        cache = KVCache(1, 8, 2, 4)
        cache.update(torch.ones(1, 2, 2, 4), torch.ones(1, 2, 2, 4), 0)
        cache.reset()
        assert float(cache.k.sum()) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Tokenizer
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenizer:
    def test_encode_decode_roundtrip(self):
        tok = BPETokenizer()
        text = 'Hello, world!'
        ids = tok.encode(text)
        assert len(ids) > 0
        decoded = tok.decode(ids)
        assert decoded == text

    def test_special_tokens(self):
        tok = BPETokenizer()
        assert tok.BOS == 2
        assert tok.EOS == 3

    def test_encode_with_special(self):
        tok = BPETokenizer()
        ids = tok.encode_with_special('hi', add_bos=True, add_eos=True)
        assert ids[0] == tok.BOS
        assert ids[-1] == tok.EOS

    def test_empty(self):
        tok = BPETokenizer()
        assert tok.encode('') == []

    def test_unicode(self):
        tok = BPETokenizer()
        text = 'Привет мир 🌍'
        ids = tok.encode(text)
        decoded = tok.decode(ids)
        assert decoded == text

    def test_apply_merge(self):
        seq = [10, 20, 10, 20, 30]
        result = _apply_merge(seq, (10, 20), 99)
        assert result == [99, 99, 30]

    def test_train_creates_merges(self):
        tok = BPETokenizer()
        corpus = 'aaaa bbbb aaaa bbbb cccc' * 50
        initial_vocab = tok.vocab_size
        tok.train(corpus, target_vocab=initial_vocab + 10)
        assert tok.vocab_size > initial_vocab
        assert len(tok.merges) > 0

    def test_save_load(self, tmp_path):
        tok = BPETokenizer()
        tok.train('hello world ' * 100, target_vocab=tok.vocab_size + 5)
        path = str(tmp_path / 'tok.json')
        tok.save(path)

        tok2 = BPETokenizer.load(path)
        assert tok2.vocab_size == tok.vocab_size
        assert tok2.merges == tok.merges

    def test_trained_roundtrip(self):
        tok = BPETokenizer()
        corpus = 'the quick brown fox jumps over the lazy dog ' * 100
        tok.train(corpus, target_vocab=tok.vocab_size + 20)
        text = 'the quick brown fox'
        assert tok.decode(tok.encode(text)) == text


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Model
# ═══════════════════════════════════════════════════════════════════════════════

class TestModel:
    def test_forward_shape(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tokens = torch.randint(0, cfg.vocab_size, (2, 10))
        logits = model(tokens)
        assert logits.shape == (2, 10, cfg.vocab_size)

    def test_with_cache(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        model.eval()
        caches = model.create_caches(1)

        prompt = torch.randint(0, cfg.vocab_size, (1, 5))
        logits1 = model(prompt, start_pos=0, caches=caches)
        assert logits1.shape == (1, 5, cfg.vocab_size)

        next_tok = torch.randint(0, cfg.vocab_size, (1, 1))
        logits2 = model(next_tok, start_pos=5, caches=caches)
        assert logits2.shape == (1, 1, cfg.vocab_size)

    def test_embed_text(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tokens = torch.randint(0, cfg.vocab_size, (1, 8))
        emb = model.embed_text(tokens)
        assert emb.shape == (1, cfg.dim)

    def test_count_parameters(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        assert model.count_parameters() > 0

    def test_tied_weights(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        assert model.head.weight is model.embed.weight

    def test_rmsnorm(self):
        norm = RMSNorm(32)
        x = torch.randn(2, 5, 32)
        out = norm(x)
        assert out.shape == x.shape

    def test_rope_shape(self):
        freqs = precompute_rope(16, 64)
        assert freqs.shape == (64, 8)   # dim/2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Inference Engine
# ═══════════════════════════════════════════════════════════════════════════════

class TestInference:
    def test_generate_returns_text_and_embedding(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        engine = InferenceEngine(model, tok, cfg)

        text, emb = engine.generate('hello', max_new_tokens=5)
        assert isinstance(text, str)
        assert emb.shape == (cfg.dim,)

    def test_greedy(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        engine = InferenceEngine(model, tok, cfg)

        text = engine.generate_greedy('test', max_new_tokens=3)
        assert isinstance(text, str)

    def test_total_tokens(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        engine = InferenceEngine(model, tok, cfg)
        engine.generate('hi', max_new_tokens=2)
        assert engine.total_tokens > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Memory Manager
# ═══════════════════════════════════════════════════════════════════════════════

class TestMemory:
    def test_store_and_recall_working(self):
        mm = MemoryManager(embedding_dim=32)
        emb = torch.randn(32)
        mm.store_working('hello', emb)
        assert len(mm.working) == 1

    def test_recall_cosine(self):
        mm = MemoryManager(embedding_dim=16)
        e1 = torch.tensor([1.0] * 8 + [0.0] * 8)
        e2 = torch.tensor([0.0] * 8 + [1.0] * 8)
        mm.store_episodic('similar', e1, importance=0.5)
        mm.store_episodic('different', e2, importance=0.5)

        query = torch.tensor([1.0] * 8 + [0.0] * 8)
        results = mm.recall(query, k=1, scope='episodic')
        assert len(results) == 1
        assert results[0].text == 'similar'

    def test_episodic_eviction(self):
        mm = MemoryManager(embedding_dim=8, episodic_capacity=3)
        for i in range(5):
            mm.store_episodic(f'entry_{i}', torch.randn(8), importance=float(i))
        assert len(mm.episodic) == 3
        # Должны остаться записи с наибольшей importance
        imps = [e.importance for e in mm.episodic]
        assert min(imps) >= 2.0

    def test_save_load(self, tmp_path):
        mm = MemoryManager(embedding_dim=8)
        mm.store_episodic('fact1', torch.randn(8))
        mm.store_semantic('fact2', torch.randn(8))
        path = str(tmp_path / 'mem.pt')
        mm.save(path)

        mm2 = MemoryManager(embedding_dim=8)
        mm2.load(path)
        assert len(mm2.episodic) == 1
        assert len(mm2.semantic) == 1

    def test_stats(self):
        mm = MemoryManager(embedding_dim=8)
        mm.store_working('w', torch.randn(8))
        mm.store_episodic('e', torch.randn(8))
        s = mm.stats()
        assert s['working'] == 1
        assert s['episodic'] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Action Loop
# ═══════════════════════════════════════════════════════════════════════════════

class TestActionLoop:
    def test_step_returns_string(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        engine = InferenceEngine(model, tok, cfg)
        memory = MemoryManager(cfg.dim)
        loop = ActionLoop(engine, memory)

        result = loop.step('hello')
        assert isinstance(result, str)

    def test_step_stores_memories(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        engine = InferenceEngine(model, tok, cfg)
        memory = MemoryManager(cfg.dim)
        loop = ActionLoop(engine, memory)

        loop.step('hello world this is a test input')
        assert len(memory.working) >= 2   # user + assistant

    def test_parse_structured_output(self):
        text = '<think>planning</think> some text <act>respond</act> <mem>remember this</mem>'
        parsed = parse_structured_output(text)
        assert parsed['think'] == ['planning']
        assert parsed['act'] == ['respond']
        assert parsed['mem'] == ['remember this']
        assert 'some text' in parsed['raw']

    def test_parse_no_tags(self):
        text = 'just plain text'
        parsed = parse_structured_output(text)
        assert parsed['raw'] == 'just plain text'
        assert parsed['think'] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Brain (orchestrator)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrain:
    def test_think(self):
        brain = BareMetalBrain(config=_tiny_cfg())
        result = brain.think('hello')
        assert isinstance(result, str)

    def test_infer_compatible(self):
        brain = BareMetalBrain(config=_tiny_cfg())
        result = brain.infer(
            prompt='test',
            system='you are helpful',
            history=[{'role': 'user', 'content': 'hi'}],
            max_tokens=3,
        )
        assert isinstance(result, str)

    def test_health(self):
        brain = BareMetalBrain(config=_tiny_cfg())
        h = brain.health()
        assert h['ok'] is True
        assert h['backend'] == 'bare_metal'
        assert h['params'] > 0

    def test_total_tokens_increments(self):
        brain = BareMetalBrain(config=_tiny_cfg())
        brain.think('test')
        assert brain.total_tokens > 0

    def test_cost_is_zero(self):
        brain = BareMetalBrain(config=_tiny_cfg())
        assert brain.total_cost_usd == 0.0

    def test_save_load_checkpoint(self, tmp_path):
        cfg = _tiny_cfg()
        brain = BareMetalBrain(config=cfg)
        brain.think('remember this')

        ckpt_dir = str(tmp_path / 'ckpt')
        brain.save_checkpoint(ckpt_dir)

        # Проверяем файлы
        assert os.path.exists(os.path.join(ckpt_dir, 'config.json'))
        assert os.path.exists(os.path.join(ckpt_dir, 'model.pt'))
        assert os.path.exists(os.path.join(ckpt_dir, 'tokenizer.json'))

        # Загрузка
        brain2 = BareMetalBrain(checkpoint_dir=ckpt_dir)
        assert brain2.config.dim == cfg.dim
        assert brain2.model.count_parameters() == brain.model.count_parameters()

    def test_train_tokenizer(self):
        brain = BareMetalBrain(config=_tiny_cfg())
        initial = brain.tokenizer.vocab_size
        brain.train_tokenizer('hello world ' * 100, target_vocab=initial + 5)
        assert brain.tokenizer.vocab_size > initial


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Training
# ═══════════════════════════════════════════════════════════════════════════════

class TestTraining:
    def test_train_on_text(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        trainer = Trainer(model, tok, cfg, lr=1e-3)

        corpus = 'the quick brown fox jumps over the lazy dog ' * 100
        result = trainer.train_on_text(corpus, epochs=1, batch_size=2, seq_len=32)
        assert result['loss'] > 0
        assert result['batches'] > 0

    def test_short_text_noop(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        trainer = Trainer(model, tok, cfg)

        result = trainer.train_on_text('hi', epochs=1, seq_len=512)
        assert result['batches'] == 0

    def test_loss_decreases(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        trainer = Trainer(model, tok, cfg, lr=1e-2)

        corpus = 'aaaa bbbb cccc dddd ' * 200
        r1 = trainer.train_on_text(corpus, epochs=1, batch_size=2, seq_len=32)
        r2 = trainer.train_on_text(corpus, epochs=1, batch_size=2, seq_len=32)
        # Loss после второго прогона должен быть меньше
        assert r2['loss'] < r1['loss']

    def test_step_count(self):
        cfg = _tiny_cfg()
        model = BareMetalTransformer(cfg)
        tok = BPETokenizer()
        trainer = Trainer(model, tok, cfg)
        trainer.train_on_text('test data ' * 100, epochs=1, batch_size=1, seq_len=16)
        assert trainer.step_count > 0
