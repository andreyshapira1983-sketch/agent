import sys; sys.path.insert(0,".")
from dotenv import load_dotenv; load_dotenv(".env")
from core.answer_format import format_artifact
from core.injection_guard import annotate_suspicious
from core.llm import LLM

q="Что ты видишь из файла knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md и сколько знаков? Назови число знаков, дошедших до тебя, и процитируй первую и последнюю строку из того, что видишь. Отвечай коротко."
text=open("knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",encoding="utf-8").read()
block=format_artifact("file_read", text, question=q)
llm=LLM(provider="deepseek", model="deepseek-chat")
system="Ты отвечаешь только по приведённым уликам. Не выдумывай."
for label, ev in (("wrapped", annotate_suspicious(block,"file:knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md")), ("plain", block)):
    user=f"Улики:\n<<<\n{ev}\n>>>\n\nВопрос: {q}"
    out=llm.complete(system=system, user=user, max_tokens=400, temperature=0.0)
    print(f"=== {label} ({len(ev)} chars of evidence) ===\n{out.strip()[:700]}\n", flush=True)
