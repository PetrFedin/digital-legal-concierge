from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
env = root / '.env'
errors = []
if not env.exists():
    errors.append('Нет .env. Скопируйте .env.example в .env')
else:
    text = env.read_text(encoding='utf-8')
    if 'BOT_TOKEN=CHANGE_ME' in text:
        errors.append('BOT_TOKEN не заменен. Вставьте токен от BotFather')
if errors:
    print('НЕ ГОТОВО:')
    for e in errors:
        print('-', e)
    sys.exit(1)
print('OK: базовые настройки готовы')
