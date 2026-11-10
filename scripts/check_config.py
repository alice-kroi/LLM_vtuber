import configparser
cfg = configparser.ConfigParser()
cfg.read('config.ini', encoding='utf-8')
print(f'Sections: {cfg.sections()}')
if cfg.has_section('model'):
    print(f'[model] model = {cfg.get("model", "model", fallback="NOT_FOUND")}')
if cfg.has_section('llm'):
    print(f'[llm] model = {cfg.get("llm", "model", fallback="NOT_FOUND")}')