# Стенд адаптивного тестирования асинхронных агентов

Стенд проверяет, как задержки сообщений и порядок событий влияют на взаимодействующих агентов. Он задаёт допустимое расписание, сохраняет решения среды и позволяет точно повторить траекторию или продолжить исполнение из сохранённого префикса.

| Компонент | Назначение |
| --- | --- |
| [Событийная лаборатория](laboratory/backend/src/cp1/) | «Локальный» и RAPS, транспорт FIFO, запись, проверка, повтор и ветвление |
| [Поисковый цикл](laboratory/backend/src/adaptive_search/) | Адаптеры «Локального» и Max-Sum, выбор испытаний и учёт затрат |
| [Max-Sum](laboratory/backend/src/adaptive_search/maxsum.py) | Асинхронное распределение каналов и точный эталон малой задачи |
| [Веб-интерфейс](laboratory/frontend/) | Редактор условий консенсуса, просмотр исполнения и сохранённых сравнений |
| [Входные конфигурации](laboratory/scenarios/) | Малые примеры графов для запуска |
| [Тесты Python](laboratory/backend/tests/), [тесты TypeScript](laboratory/frontend/tests/) | Свойства алгоритмов, транспорта, повторения и поискового цикла |

## Установка и запуск

Нужны Python 3.12. Для интерфейса также нужны Node.js 22+ и pnpm 11. Из корня репозитория на Windows:

```powershell
.\laboratory\scripts\setup.ps1
.\laboratory\scripts\start.ps1
```

Страница доступна на <http://127.0.0.1:8765>. У `start.ps1` есть параметры `-Port` и `-DataPath`. Скрипт установки принимает явные пути `-PythonPath`, `-NodePath` и `-PnpmPath`.

Для расчётного режима в отдельном окружении Python 3.12:

```shell
python -m pip install -e "./laboratory/backend[test]"
python -m pytest laboratory/backend/tests -m "not spade"
python -m cp1 run --configuration laboratory/scenarios/ring-events.json --seed 42 --output local-data/ring.zip
python -m cp1 verify local-data/ring.zip
python -m cp1 replay local-data/ring.zip --output local-data/ring-replay.zip
python -m adaptive_search --algorithm maxsum --profile tree --trials 1 --warmup 1 --method independent --seed 42
```

`--algorithm local` включает «Локальный». `--profile development` выбирает рабочий пример адаптера. `--output local-data/search.json` сохраняет журнал одной сессии. Все параметры показывает `python -m adaptive_search --help`. Max-Sum доступен в расчётном режиме. Веб-интерфейс предназначен для консенсуса.

## Архитектура

- `cp1.event_engine` выбирает времена в миллисекундах, доставляет сообщения через FIFO и записывает состояния. `cp1.event_validator` проверяет запись отдельно от исполнителя.
- `adaptive_search.search.Problem` задаёт общий контракт. Адаптер исполняет сценарий, строит предложения, повторяет запись и валидирует её. Политика работает через этот контракт.
- Сигналы сообщений находятся в `adaptive_search.fuzz_feedback`, предвестники точек ветвления — в `adaptive_search.early_cut`. Сравнение по обработанным событиям включает повтор префикса.
- «Локальный» основан на [mean_consensus_spade](https://github.com/AnnaKasatkina/mean_consensus_spade). Копия и лицензия сохранены в [vendor](laboratory/vendor/mean_consensus_spade/). RAPS опирается на [Spiridonoff и соавт.](https://jmlr.org/papers/v21/18-813.html), Max-Sum — на [Zivan и соавт.](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.CP.2021.60).

Репозиторий содержит реализацию, тесты, входные конфигурации и файлы зависимостей. Результаты испытаний, отчёты, презентации и базы истории хранятся локально. Для выходных файлов предназначена игнорируемая папка `local-data/`.
