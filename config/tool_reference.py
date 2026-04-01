"""
Полный справочник инструментов — точные сигнатуры для LLM.

Используется в CognitiveCore как системная подсказка.
Принцип: один рабочий пример на каждый инструмент.
"""

TOOL_REFERENCE = '''
=== ИНСТРУМЕНТЫ АГЕНТА (вызов через tool_layer.use) ===
Все файлы сохраняй в папку outputs/. Используй ТОЧНЫЕ имена параметров.

──────────────────────────────────────────────
ФАЙЛОВАЯ СИСТЕМА
──────────────────────────────────────────────
# Чтение файла
r = tool_layer.use('filesystem', action='read', path='README.md')
print(r['content'])

# Запись файла
r = tool_layer.use('filesystem', action='write', path='outputs/result.txt', content='Текст')

# Добавить в файл
r = tool_layer.use('filesystem', action='append', path='outputs/log.txt', content='Строка\n')

# Список файлов в папке
r = tool_layer.use('filesystem', action='list', path='outputs/')
print(r['files'])

# Проверить существование
r = tool_layer.use('filesystem', action='exists', path='outputs/file.txt')
print(r['exists'])  # True / False

# Удалить файл
r = tool_layer.use('filesystem', action='delete', path='outputs/old.txt')

──────────────────────────────────────────────
ТЕРМИНАЛ / КОМАНДЫ
──────────────────────────────────────────────
r = tool_layer.use('terminal', command='echo Hello World')
r = tool_layer.use('terminal', command='ls outputs/', timeout=10)
print(r['output'])   # stdout
print(r['error'])    # stderr

──────────────────────────────────────────────
PYTHON — ИСПОЛНЕНИЕ КОДА
──────────────────────────────────────────────
code = """
import datetime
with open('outputs/hello.txt', 'w') as f:
    f.write(f'Hello {datetime.date.today()}')
print('done')
"""
r = tool_layer.use('python_runtime', code=code)
print(r['output'])   # stdout
print(r['error'])    # ошибки

──────────────────────────────────────────────
PDF
──────────────────────────────────────────────
# Из текста
r = tool_layer.use('pdf_generator', action='from_text',
    output='outputs/report.pdf',
    title='Заголовок отчёта',
    text='Содержимое документа.\n\nВторой абзац.')
print(r['success'], r.get('path'))

# Из HTML
r = tool_layer.use('pdf_generator', action='from_html',
    output='outputs/page.pdf',
    html='<h1>Заголовок</h1><p>Текст</p>')

# Объединить несколько PDF
r = tool_layer.use('pdf_generator', action='merge',
    output='outputs/merged.pdf',
    files=['outputs/a.pdf', 'outputs/b.pdf'])

──────────────────────────────────────────────
EXCEL / SPREADSHEET
──────────────────────────────────────────────
# Записать данные (action='write', rows=список словарей)
r = tool_layer.use('spreadsheet', action='write',
    path='outputs/data.xlsx',
    rows=[
        {'Имя': 'Алиса', 'Оценка': 95, 'Дата': '2025-01-01'},
        {'Имя': 'Боб',   'Оценка': 87, 'Дата': '2025-01-02'},
    ])
print(r['success'], r.get('path'))

# Прочитать данные
r = tool_layer.use('spreadsheet', action='read', path='data.xlsx')
print(r['rows'])  # список словарей

# Добавить строки
r = tool_layer.use('spreadsheet', action='append',
    path='outputs/data.xlsx',
    rows=[{'Имя': 'Карл', 'Оценка': 78}])

──────────────────────────────────────────────
POWERPOINT / ПРЕЗЕНТАЦИЯ
──────────────────────────────────────────────
# Создать презентацию
r = tool_layer.use('powerpoint', action='create',
    output='outputs/slides.pptx',
    title='Заголовок презентации',
    slides=[
        {'title': 'Слайд 1', 'content': 'Текст первого слайда'},
        {'title': 'Слайд 2', 'content': 'Текст второго слайда\n- Пункт 1\n- Пункт 2'},
    ])
print(r['success'], r.get('path'))

# Из текста (автоматическое разбиение на слайды)
r = tool_layer.use('powerpoint', action='from_text',
    output='outputs/auto_slides.pptx',
    title='Авто-презентация',
    text='Весь текст — будет разбит на слайды автоматически.')

──────────────────────────────────────────────
ГРАФИКИ / ВИЗУАЛИЗАЦИЯ
──────────────────────────────────────────────
# Столбчатый (bar)
r = tool_layer.use('data_viz', action='bar',
    output='outputs/bar.png',
    data={'Продажи': [10, 25, 18, 30, 22]},
    labels=['Пн', 'Вт', 'Ср', 'Чт', 'Пт'],
    title='Продажи за неделю')

# Линейный (line)
r = tool_layer.use('data_viz', action='line',
    output='outputs/line.png',
    data={'Температура': [18, 20, 22, 19, 17]},
    labels=['Пн', 'Вт', 'Ср', 'Чт', 'Пт'],
    title='Температура')

# Круговой (pie) — data=список чисел
r = tool_layer.use('data_viz', action='pie',
    output='outputs/pie.png',
    data=[40, 35, 25],
    labels=['Python', 'JS', 'Other'],
    title='Языки программирования')

# Точечный (scatter)
r = tool_layer.use('data_viz', action='scatter',
    output='outputs/scatter.png',
    data={'Точки': [1, 4, 9, 16, 25]},
    labels=['1', '2', '3', '4', '5'],
    title='Квадраты')
print(r['success'], r.get('path'))

──────────────────────────────────────────────
СКРИНШОТ
──────────────────────────────────────────────
# Весь экран
r = tool_layer.use('screenshot', save_path='outputs/screen.png')

# Конкретный монитор
r = tool_layer.use('screenshot', save_path='outputs/monitor2.png', monitor=1)
print(r['success'], r.get('path'))

──────────────────────────────────────────────
ПЕРЕВОД
──────────────────────────────────────────────
r = tool_layer.use('translate', text='Hello world', target='ru')
r = tool_layer.use('translate', text='Привет мир', target='en')
r = tool_layer.use('translate', text='Hello', target='he', source='en')
print(r['translated'])   # переведённый текст
print(r['backend'])      # 'openai' или 'deep_translator'

──────────────────────────────────────────────
ПОИСК В ИНТЕРНЕТЕ
──────────────────────────────────────────────
r = tool_layer.use('search', query='Python 3.12 new features', num_results=5)
for item in r.get('results', []):
    print(item['title'], item['url'])
    print(item['snippet'])

──────────────────────────────────────────────
EMAIL
──────────────────────────────────────────────
# Отправить письмо
r = tool_layer.use('email', action='send',
    to='recipient@example.com',
    subject='Отчёт агента',
    body='Привет! Вот отчёт за сегодня.')
print(r['success'])

# Отправить с вложением
r = tool_layer.use('email', action='send',
    to='boss@company.com',
    subject='Файл',
    body='Во вложении файл.',
    attachments=['outputs/report.pdf'])

# Прочитать входящие
r = tool_layer.use('email', action='read', limit=10)
for msg in r.get('messages', []):
    print(msg['subject'], msg['from'])

──────────────────────────────────────────────
АРХИВЫ
──────────────────────────────────────────────
# Создать ZIP
r = tool_layer.use('archive', action='create',
    archive_path='outputs/backup.zip',
    files=['outputs/report.pdf', 'outputs/data.xlsx'])
print(r['success'], r.get('path'))

# Распаковать
r = tool_layer.use('archive', action='extract',
    archive_path='backup.zip',
    extract_to='outputs/extracted/')

# Список файлов в архиве
r = tool_layer.use('archive', action='list',
    archive_path='backup.zip')
print(r['files'])

──────────────────────────────────────────────
HTTP ЗАПРОСЫ
──────────────────────────────────────────────
# GET запрос
r = tool_layer.use('http_client',
    url='https://api.github.com/repos/python/cpython',
    method='GET')
print(r['status_code'], r['json'])

# POST с JSON телом
r = tool_layer.use('http_client',
    url='https://httpbin.org/post',
    method='POST',
    headers={'Content-Type': 'application/json'},
    body={'key': 'value'})
print(r['status_code'])

# Курс валют (бесплатно, без API-ключа)
r = tool_layer.use('http_client',
    url='https://api.frankfurter.app/latest?from=USD&to=ILS')
rate = r['json']['rates']['ILS']   # например: 3.74
amount_usd = 250
result_ils = round(amount_usd * rate, 2)
print(f"1 USD = {rate} ILS")
print(f"{amount_usd} USD = {result_ils} ILS")
tool_layer.use('filesystem', action='write',
    path='outputs/currency_result.txt',
    content=f"1 USD = {rate} ILS\n{amount_usd} USD = {result_ils} ILS\n")

──────────────────────────────────────────────
СЕТЬ
──────────────────────────────────────────────
# Пинг
r = tool_layer.use('network', action='ping', host='8.8.8.8', count=3)
print(r['alive'], r.get('avg_ms'))

# DNS
r = tool_layer.use('network', action='dns', host='google.com')
print(r['addresses'])

# Проверить порт
r = tool_layer.use('network', action='port_check', host='localhost', port=8080)
print(r['open'])

──────────────────────────────────────────────
ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ (DALL-E)
──────────────────────────────────────────────
r = tool_layer.use('image_generator',
    prompt='Футуристический город ночью, неон, дождь',
    size='1024x1024',
    save_path='outputs/city.png')
print(r['success'], r.get('path'))

──────────────────────────────────────────────
OCR — РАСПОЗНАВАНИЕ ТЕКСТА
──────────────────────────────────────────────
r = tool_layer.use('ocr', path='screenshot.png', language='rus+eng')
print(r['text'])

──────────────────────────────────────────────
NLP — ОБРАБОТКА ТЕКСТА
──────────────────────────────────────────────
# Ключевые слова
r = tool_layer.use('nlp', action='keywords', text='Python это язык программирования')
print(r['keywords'])

# Тональность
r = tool_layer.use('nlp', action='sentiment', text='Отличный продукт!')
print(r['sentiment'])  # positive / negative / neutral

# Суммаризация статистики
r = tool_layer.use('nlp', action='summary_stats', text='...')
print(r['word_count'], r['sentences'])

──────────────────────────────────────────────
СИСТЕМНЫЙ МОНИТОРИНГ
──────────────────────────────────────────────
# Здоровье системы (CPU, RAM, диск)
r = tool_layer.use('self_monitor', action='health')
print(r['cpu_percent'], r['ram_percent'])

# Читать логи
r = tool_layer.use('self_monitor', action='read_logs', lines=50, level='ERROR')
print(r['logs'])

# Поиск аномалий
r = tool_layer.use('self_monitor', action='scan_anomalies', lines=200)
print(r['anomalies'])

──────────────────────────────────────────────
GIT
──────────────────────────────────────────────
r = tool_layer.use('git', action='status')
r = tool_layer.use('git', action='log', n=10)
r = tool_layer.use('git', action='diff')
r = tool_layer.use('git', action='add', path='file.py')
r = tool_layer.use('git', action='commit', message='Fix: описание')
r = tool_layer.use('git', action='push')
r = tool_layer.use('git', action='pull')
r = tool_layer.use('git', action='branch', name='feature/new')
r = tool_layer.use('git', action='checkout', branch='main')
print(r['output'])

──────────────────────────────────────────────
DOCKER
──────────────────────────────────────────────
r = tool_layer.use('docker', action='ps')               # список контейнеров
r = tool_layer.use('docker', action='run',
    image='ubuntu:22.04', command='echo hello')
r = tool_layer.use('docker', action='stop', container='my_app')
r = tool_layer.use('docker', action='logs', container='my_app', tail=50)
print(r['output'])

──────────────────────────────────────────────
УПРАВЛЕНИЕ ПАКЕТАМИ
──────────────────────────────────────────────
r = tool_layer.use('package_manager', action='install', package='requests')
r = tool_layer.use('package_manager', action='uninstall', package='old_lib')
r = tool_layer.use('package_manager', action='list')
r = tool_layer.use('package_manager', action='show', package='numpy')
print(r['success'], r.get('output'))

──────────────────────────────────────────────
БАЗЫ ДАННЫХ
──────────────────────────────────────────────
# SELECT
r = tool_layer.use('database', query='SELECT * FROM users LIMIT 10')
print(r['rows'])

# INSERT
r = tool_layer.use('database',
    query='INSERT INTO logs (msg, ts) VALUES (?, ?)',
    params=['Agent started', '2025-01-01'])
print(r['success'])

──────────────────────────────────────────────
МЫШЬ И КЛАВИАТУРА
──────────────────────────────────────────────
r = tool_layer.use('mouse_keyboard', action='click', x=100, y=200)
r = tool_layer.use('mouse_keyboard', action='type', text='Привет!')
r = tool_layer.use('mouse_keyboard', action='hotkey', keys=['ctrl', 'c'])
r = tool_layer.use('mouse_keyboard', action='scroll', x=500, y=500, clicks=3)
r = tool_layer.use('mouse_keyboard', action='move', x=300, y=400, duration=0.5)
r = tool_layer.use('mouse_keyboard', action='position')  # текущая позиция

──────────────────────────────────────────────
БУФЕР ОБМЕНА
──────────────────────────────────────────────
r = tool_layer.use('clipboard', action='write', text='Текст в буфер')
r = tool_layer.use('clipboard', action='read')
print(r['text'])

──────────────────────────────────────────────
МЕНЕДЖЕР ОКОН
──────────────────────────────────────────────
r = tool_layer.use('window_manager', action='list')
r = tool_layer.use('window_manager', action='active')
r = tool_layer.use('window_manager', action='focus', title='Notepad')
r = tool_layer.use('window_manager', action='minimize', title='Chrome')
r = tool_layer.use('window_manager', action='maximize', title='Chrome')
r = tool_layer.use('window_manager', action='close', title='Notepad')

──────────────────────────────────────────────
МЕНЕДЖЕР ПРОЦЕССОВ
──────────────────────────────────────────────
r = tool_layer.use('process_manager', action='list')
r = tool_layer.use('process_manager', action='kill', pid=1234)
r = tool_layer.use('process_manager', action='info', pid=1234)
r = tool_layer.use('process_manager', action='start', command='notepad.exe')

──────────────────────────────────────────────
УВЕДОМЛЕНИЯ
──────────────────────────────────────────────
r = tool_layer.use('notification',
    title='Агент',
    message='Задача выполнена!',
    timeout=10)

──────────────────────────────────────────────
ШИФРОВАНИЕ
──────────────────────────────────────────────
r = tool_layer.use('encryption', action='generate_key')
key = r['key']

r = tool_layer.use('encryption', action='encrypt_text', text='Секрет', key=key)
encrypted = r['encrypted']

r = tool_layer.use('encryption', action='decrypt_text', encrypted=encrypted, key=key)
print(r['text'])  # 'Секрет'

──────────────────────────────────────────────
SSH
──────────────────────────────────────────────
# Подключиться
r = tool_layer.use('ssh', action='connect',
    host='192.168.1.100', port=22,
    username='admin', password='pass')

# Выполнить команду
r = tool_layer.use('ssh', action='exec', command='ls -la')
print(r['output'])

# Загрузить файл
r = tool_layer.use('ssh', action='upload',
    local_path='outputs/report.pdf',
    remote_path='/home/admin/report.pdf')

# Скачать файл
r = tool_layer.use('ssh', action='download',
    remote_path='/var/log/app.log',
    local_path='outputs/app.log')

──────────────────────────────────────────────
HUGGING FACE
──────────────────────────────────────────────
r = tool_layer.use('huggingface', action='search_models', query='text classification', limit=5)
r = tool_layer.use('huggingface', action='model_info', model_id='bert-base-uncased')
r = tool_layer.use('huggingface', action='trending')
print(r['models'])

──────────────────────────────────────────────
ЭМБЕДДИНГИ
──────────────────────────────────────────────
r = tool_layer.use('embedding', action='embed', text='Привет мир')
vector = r['vector']

r = tool_layer.use('embedding', action='similarity',
    text_a='кошка', text_b='котёнок')
print(r['score'])  # от 0 до 1

──────────────────────────────────────────────
АНАЛИЗ КОДА
──────────────────────────────────────────────
r = tool_layer.use('code_analyzer', action='analyze', path='tools/tool_layer.py')
print(r['functions'], r['classes'])

r = tool_layer.use('code_analyzer', action='complexity', code='def foo():\n    pass')
print(r['complexity'])

r = tool_layer.use('code_analyzer', action='lint', path='agent.py')
print(r['issues'])

──────────────────────────────────────────────
ОЧЕРЕДЬ ЗАДАЧ
──────────────────────────────────────────────
r = tool_layer.use('task_queue', action='push',
    task='Создать отчёт за неделю', priority=2)

r = tool_layer.use('task_queue', action='pop')
print(r['task'])

r = tool_layer.use('task_queue', action='list')
print(r['tasks'])

──────────────────────────────────────────────
СЛЕЖЕНИЕ ЗА ФАЙЛАМИ
──────────────────────────────────────────────
r = tool_layer.use('file_watcher', action='watch',
    path='outputs/', recursive=True, watch_id='my_watcher')

r = tool_layer.use('file_watcher', action='list_events', limit=20)
print(r['events'])  # список событий: created/modified/deleted

r = tool_layer.use('file_watcher', action='stop', watch_id='my_watcher')

──────────────────────────────────────────────
ПЛАНИРОВЩИК ЗАДАЧ
──────────────────────────────────────────────
r = tool_layer.use('cron_scheduler', action='schedule',
    job_id='daily_report', interval_seconds=86400)

r = tool_layer.use('cron_scheduler', action='list')
r = tool_layer.use('cron_scheduler', action='cancel', job_id='daily_report')

──────────────────────────────────────────────
GUI-АГЕНТ (видит экран, кликает)
──────────────────────────────────────────────
r = tool_layer.use('gui_agent',
    goal='Открой Блокнот и напиши: Hello from agent',
    max_steps=10,
    pause=1.0)
print(r['success'], r.get('steps_taken'))

──────────────────────────────────────────────
GITHUB
──────────────────────────────────────────────
r = tool_layer.use('github', action='get_repo', repo='owner/repo')
r = tool_layer.use('github', action='list_issues', repo='owner/repo', state='open')
r = tool_layer.use('github', action='create_issue',
    repo='owner/repo',
    title='Bug: описание',
    body='Подробности...')
r = tool_layer.use('github', action='get_file',
    repo='owner/repo', path='README.md')
print(r['content'])

──────────────────────────────────────────────
REDDIT
──────────────────────────────────────────────
r = tool_layer.use('reddit', action='me')              # инфо о себе
r = tool_layer.use('reddit', action='hot',
    subreddit='python', limit=10)
r = tool_layer.use('reddit', action='search',
    query='AI agent', subreddit='MachineLearning')
r = tool_layer.use('reddit', action='post',
    subreddit='MachineLearning',
    title='Заголовок поста',
    text='Текст поста')
print(r['url'])  # ссылка на созданный пост

──────────────────────────────────────────────
GOOGLE CALENDAR
──────────────────────────────────────────────
r = tool_layer.use('calendar', action='list_events',
    max_results=10, time_min='2025-01-01T00:00:00Z')
r = tool_layer.use('calendar', action='create_event',
    summary='Встреча с клиентом',
    start='2025-06-01T10:00:00',
    end='2025-06-01T11:00:00',
    description='Обсуждение проекта')
print(r['event_id'])

──────────────────────────────────────────────
ОБЩИЕ ПРАВИЛА
──────────────────────────────────────────────
1. Все создаваемые файлы → в папку outputs/
2. После вызова проверяй: r['success'] — True/False
3. Путь к файлу: r.get('path') или r.get('output')
4. Ошибки: r.get('error', '')
5. НЕ импортируй from tools.tool_layer — tool_layer уже доступен
6. НЕ используй action='create' для spreadsheet — только action='write'
7. НЕ пиши output=... для screenshot — только save_path=
'''
