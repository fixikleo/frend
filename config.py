import os

# ============ НАСТРОЙКИ (из переменных среды) ============
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'ЗАПОЛНИ_В_ПЕРЕМЕННЫХ_СРЕДЫ')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '0'))
ADMIN_USERNAME = 'fixikleo'          # без @
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0')) or None

# ============ СВЕЖЕСТЬ НОВОСТЕЙ ============
MAX_NEWS_AGE_HOURS = 12
MAX_LOOKBACK_ENTRIES = 30
# ============ ТЕМЫ И RSS-ЛЕНТЫ ============
TOPICS = {
    'gaming': {
        'name': '🎮 Гейминг',
        'feeds': [
            # Русскоязычные
            'https://www.goha.ru/rss',                    # GoHa.ru — ММО, киберспорт, PC/консоли [citation:21]
            'https://stopgame.ru/rss/all'                 # StopGame — обзоры и новости игр [citation:28]
            'https://shazoo.ru/feed'                      # Shazoo — игровые новости [citation:28]
            # Английские (на случай, если русские не дадут достаточно контента)
            'https://www.pcgamer.com/rss/',
        ]
    },
    'politics': {
        'name': '🏛 Политика',
        'feeds': [
            # Русскоязычные
            'https://lenta.ru/rss/news',                  # Lenta.ru — новости [citation:27]
            'https://tass.ru/rss/v2.xml',                 # ТАСС [citation:1]
            'https://ria.ru/export/rss2/politics/index.xml',  # РИА Новости — политика [citation:1]
            'https://aif.ru/rss/politics.php'             # Аргументы и Факты — политика [citation:2]
        ]
    },
    'weather': {
        'name': '🌤 Погода',
        'feeds': [
            # Русскоязычные
            'https://meteoinfo.ru/rss/forecasts',         # Гидрометцентр России [citation:5]
            'http://informer.gismeteo.ru/rss/27612.xml'   # Gismeteo — Москва (id города можно поменять) [citation:30]
        ]
    },
    'tech': {
        'name': '💻 Технологии',
        'feeds': [
            # Русскоязычные
            'https://3dnews.ru/news/rss',                 # 3DNews — IT и цифровые технологии [citation:16]
            'https://habr.com/ru/rss/news/',              # Habr — новости
            'https://www.ixbt.com/export/news.rss',       # iXBT
            'https://cnews.ru/rss/'         # CNews — ИТ и телеком [citation:3]
        ]
    },
    'sport': {
        'name': '⚽ Спорт',
        'feeds': [
            # Русскоязычные
            'https://www.sport-express.ru/rss/'            # Спорт-Экспресс [citation:11]
            'https://www.championat.com/rss/news.xml',    # Чемпионат
            'https://aif.ru/rss/sport.php'                # АиФ — спорт [citation:2]
        ]
    },
    'science': {
        'name': '🔬 Наука',
        'feeds': [
            # Русскоязычные
            'https://nplus1.ru/rss',                      # N+1 [citation:3]
            'https://naked-science.ru/rss'                # Naked Science [citation:25]
            'https://sbras.info/rss'                      # Наука в Сибири [citation:32]
        ]
    },
    'security': {
        'name': '🛡 Кибербезопасность',
        'feeds': [
            # Русскоязычные
            'https://www.securitylab.ru/_Services/Export/RSS/news/',  # SecurityLab — новости [citation:37]
            'https://xakep.ru/feed/'                      # Xakep.ru [citation:37]
            'https://bdu.fstec.ru/ubi/vul/rss'            # ФСТЭК БДУ — уязвимости [citation:24]
        ]
    },
}
