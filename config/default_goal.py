"""
Default autonomous goal — выполняется каждый цикл агента.
tool_layer уже доступен в sandbox — не нужно его импортировать.
"""

DEFAULT_GOAL = """Создай три файла и сохрани их в папку outputs/.

Шаг 1:
```python
import datetime, psutil, json
data = {
    "cpu": psutil.cpu_percent(1),
    "ram": psutil.virtual_memory().percent,
    "disk": psutil.disk_usage("C:\\\\").percent,
    "time": str(datetime.datetime.now())
}
with open("outputs/health.json", "w") as f:
    json.dump(data, f, indent=2)
print("health.json OK:", data["cpu"], "% CPU,", data["ram"], "% RAM")
```

Шаг 2:
```python
import datetime
result = tool_layer.use("pdf_generator", action="from_text",
    output="outputs/daily_report.pdf",
    title=f"Daily Agent Report {datetime.date.today()}",
    text=f"Autonomous AI Agent\\nDate: {datetime.date.today()}\\nStatus: Running\\nTools: 48 active\\nUpwork: https://www.upwork.com/services/product/2038909844504654059")
print("PDF:", result.get("success"), result.get("path"))
```

Шаг 3:
```python
import datetime
result = tool_layer.use("spreadsheet", action="write",
    path="outputs/daily_log.xlsx",
    rows=[{
        "Date": str(datetime.date.today()),
        "Time": datetime.datetime.now().strftime("%H:%M"),
        "Status": "OK",
        "Files": "health.json, daily_report.pdf, daily_log.xlsx"
    }])
print("Excel:", result.get("success"), result.get("path"))
```
"""
