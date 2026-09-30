# Бенчмарк вопросов по dashboard 44

Этот benchmark проверяет сценарий, в котором сотрудник call-центра открыл
dashboard **«IVR детальная информация»** (`dashboard_id=44`) и задаёт агенту
обычные рабочие вопросы по тому, что видит на экране.

Вопросы намеренно не содержат имён БД, таблиц и колонок. Runner передаёт
нативный Superset `page_context`: dashboard, активную вкладку, видимые графики и
применённый временной фильтр. Агент должен сам открыть контекст dashboard/chart,
найти источник и выполнить SQL.

`golden_sql`, ожидаемые факты и правила scorer-а существуют только во внешнем
evaluator-е и никогда не добавляются в сообщение или системный prompt агента.

## Дизайн

Канонический набор `cases_release.json` содержит 26 задач:

| Контекст | Кейсов | Что покрывается |
| --- | ---: | --- |
| Общий график | 1 | пик количества уникальных звонков |
| `За месяц` | 8 | автоматизация, пути завершения, нажатия, инструменты, повторы после менеджера |
| `IVR` | 7 | пункты меню, языки, полисы, статус/заявка, запись номера |
| `Робот` | 9 | исходы робот-ветки, классы, уверенность, бизнес-интенты, спам |
| Весь dashboard | 1 | сводка перед сменой по трём вкладкам |

Покрыты все 13 графиков, реально находящихся в layout dashboard 44:

- вне вкладок: 269;
- `За месяц`: 268, 275, 276, 277, 278;
- `IVR`: 272, 273;
- `Робот`: 270, 271, 274, 279, 280.

Chart 269 находится в корневом GRID вне вкладок, поэтому включён в `page_context`
каждого кейса как постоянно видимый график.

Для воспроизводимости во всех кейсах зафиксирован один и тот же фильтр:
`[2026-07-12, 2026-08-12)`. Это часть `page_context`, а не подсказка в prompt.
`goldens_release.json` содержит заранее выполненную однострочную выдачу каждого
golden SQL на `database_id=3`.

`cases.json` — четыре репрезентативных smoke-кейса; `cases_release.json` — весь
набор. Релизный результат считается стабильным только после трёх последовательных
повторов.

## Проверки

Кейс проходит, если агент завершил run, вызвал обязательный `execute_sql`
и верно ответил. SQL evidence сохраняется для диагностики, но сама форма
запроса не является hard gate.

| Гейт | Проверка |
| --- | --- |
| Completion | Run завершён успешно и агент вернул непустой ответ |
| SQL evidence | Успешный SQL и нужные golden-факты в tool result; `sql_must_match` остаётся только диагностикой |
| Answer correctness | Все запрошенные числа, даты и категории присутствуют в ответе; числа сравниваются после округления до 1 знака, формулировка метки не является гейтом |
| Answer consistency | Явно запрещённые противоречивые дополнительные утверждения отсутствуют |
| Tool contract | `execute_sql` действительно вызван; диагностические metadata-tools не являются обязательным маршрутом |

SQL tool call остаётся обязательным для всех 26 задач. Текстовая похожесть и
LLM-as-a-judge не используются для проверки фактов.

У факта `answer_required=false` значение остаётся в oracle и SQL evidence, но
не требуется в тексте, если пользователь его не спрашивал. `labels` сохраняются
только для диагностики связи числа с формулировкой, а не как hard gate.

Создание charts/dashboards и safety refusal не входят в этот набор: это другие
пользовательские сценарии. Сам runner всё ещё умеет их проверять, но смешивание
таких кейсов с вопросами сотрудника call-центра исказило бы основной показатель.

## Известные проблемы исходного dashboard

В сохранённых настройках charts 271 и 274 используется строка даты
`2025-26-12`, которая вызывает SQL Server error 242. По смыслу порога oracle
использует однозначную ISO-дату `2025-12-26`. Это дефект конфигурации конкретных
графиков, а не подтверждённый дефект сервиса Superset; он не скрыт и может
проявиться в trace, если агент слепо скопирует выражение графика.

Любая другая явная ошибка сервиса сохраняется в локальном отчёте и Langfuse со
всем tool trail. Её нельзя превращать в исключение scorer-а или обходить prompt-ом.
После отделения от model/data/config/environment fault для open-source сервиса
делается минимальный upstream fix с regression test и PR в официальный проект.

## Проверка manifest и runner

Из каталога `deploy`:

```bash
python3 benchmark/generate_cases.py
python3 -m unittest benchmark/test_run.py
```

Генератор детерминированно обновляет `cases_release.json` и `cases.json`.
Проверенные frozen rows можно экспортировать из полного live-run без
`--goldens`:

```bash
python3 benchmark/generate_cases.py \
  --goldens-from-report benchmark/results/live-oracle.json
```

До фиксации нового `goldens_release.json` каждый golden SQL должен успешно
вернуть ровно одну строку на целевой БД.

## Smoke-запуск

```bash
cd deploy
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -e SUPERSET_BENCHMARK_URL=http://superset:8088 \
  -e SUPERSET_BENCHMARK_PUBLIC_URL=http://10.48.140.17:8088 \
  -v "$PWD/benchmark:/benchmark" \
  superset python /benchmark/run.py \
    --cases /benchmark/cases.json \
    --goldens /benchmark/goldens_release.json \
    --output-dir /benchmark/results/dashboard-44-smoke \
    --run-name dashboard-44-smoke \
    --no-langfuse --protect-database datamart_106
```

## Полный запуск

```bash
cd deploy
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -e SUPERSET_BENCHMARK_URL=http://superset:8088 \
  -e SUPERSET_BENCHMARK_PUBLIC_URL=http://10.48.140.17:8088 \
  -v "$PWD/benchmark:/benchmark" \
  superset python /benchmark/run.py \
    --cases /benchmark/cases_release.json \
    --goldens /benchmark/goldens_release.json \
    --output-dir /benchmark/results/dashboard-44-v1 \
    --run-name superset-native-ai-dashboard-44-v1-r1 \
    --resume --protect-database datamart_106
```

Три повтора и aggregate:

```bash
mkdir -p benchmark/results/dashboard-44-v1
nohup setsid benchmark/run_release_gated.sh \
  > benchmark/results/dashboard-44-v1/suite.log 2>&1 < /dev/null &
```

Runner выполняет кейсы последовательно. Frozen goldens убирают повторный oracle
query из каждого прогона; live остаётся SQL самого агента. `--case ID` запускает
выбранные кейсы, `--resume` продолжает checkpoint, `--no-langfuse` отключает
публикацию, `--keep-threads` сохраняет чаты для ручного разбора.

## Langfuse

Dataset называется `superset-native-ai-dashboard-44-v1`. В dataset input вместе
с prompt сохраняется тот же `page_context`, который получил агент. Каждый case
связывается с Superset trace и получает scores:

- `bench.overall`;
- `bench.completion`;
- `bench.sql`;
- `bench.answer`;
- `bench.tool_contract`;
- `bench.tool_success_rate`;
- `bench.latency_seconds`.

Локальный JSON остаётся полной диагностикой: prompt, page context, golden rows,
SQL агента, tool calls, ошибки и Langfuse trace ID.
