#!/usr/bin/env python3
"""Generate the dashboard-44 call-center question benchmark."""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path
from typing import Any


DATABASE_ID = 3
DASHBOARD_ID = 44
START = "2026-07-12"
END = "2026-08-12"
CUTOFF = "2025-12-26"
SUMMARY = "dbo.texas_unique_callers_summary"
CALLS = "dbo.texas_call_center"
ROBOT = "dbo.robot_call_logs_table"

GLOBAL_CHARTS = [(269, "Количества звонков")]
TAB_CHARTS = {
    "За месяц": [
        (268, "Доля перезвонов"),
        (275, "По уникальным клиентам где закончили в течение дня"),
        (276, "Уровень автоматизаций"),
        (277, "Доля нажатий"),
        (278, "Автоматизация по инструментам"),
    ],
    "IVR": [
        (272, "Выбор в ivr"),
        (273, "Когда не получилось записать номер"),
    ],
    "Робот": [
        (270, "Доля автоматизации от звонков Робот"),
        (271, "Уверенность модели доля"),
        (274, "Распределение классов"),
        (279, "Кол-во звонков по классам"),
        (280, "Доля автоматизации от звонков Робот"),
    ],
}

IVR_CHOICES = (
    "'kz_polis_instr', 'kz_polis_get', 'kz_cc_status', "
    "'kz_cc_application', 'kz_cc_doc_vid', 'ru_polis_get', "
    "'ru_cc_status', 'ru_cc_application', 'ru_cc_doc_vid'"
)

IVR_CHOICE_LABEL = """
CASE
  WHEN ivr_choice = 'kz_polis_instr' THEN N'Каз. Инструкция оформление'
  WHEN ivr_choice = 'kz_polis_get' THEN N'Каз. Скачать полис'
  WHEN ivr_choice = 'kz_cc_status' THEN N'Каз. Статус заявки'
  WHEN ivr_choice = 'kz_cc_application' THEN N'Каз. СС инструкция'
  WHEN ivr_choice = 'kz_cc_doc_vid' THEN N'Каз. Инструкция загрузки видео/доков'
  WHEN ivr_choice = 'ru_polis_get' THEN N'Рус. Скачать полис'
  WHEN ivr_choice = 'ru_cc_status' THEN N'Рус. Статус заявки'
  WHEN ivr_choice = 'ru_cc_application' THEN N'Рус. СС инструкция'
  WHEN ivr_choice = 'ru_cc_doc_vid' THEN N'Рус. Инструкция загрузки видео/доков'
END
""".strip()

ROBOT_CLASS = """
CASE
  WHEN response = 'custom/manager' THEN N'Менеджер'
  WHEN response = 'custom/buy_policy' THEN N'Новый полис'
  WHEN response IN (
    'custom/update_tech_passport_data', 'custom/add_or_replace_vehicle',
    'custom/add_or_replace_driver', 'custom/pereoformit'
  ) THEN N'Переоформление'
  WHEN response IN ('custom/calculate_price', 'custom/raschitat')
    THEN N'Рассчитать стоимость'
  WHEN response IN ('custom/terminate_policy', 'custom/rastor_polis')
    THEN N'Расторгнуть'
  WHEN response IN ('custom/renew_policy', 'custom/prodlit_polis')
    THEN N'Продлить'
  WHEN response IN (
    'custom/allo', 'custom/da', 'custom/net', 'custom/no', 'custom/yes',
    'custom/no_thanks', 'custom/mat', 'custom/try_before_manager'
  ) THEN N'Спам'
  WHEN response IN ('custom/download_or_check_policy', 'custom/skachat_polis')
    THEN N'Скачать полис'
  WHEN response = '' THEN N'Пусто'
  ELSE N'Остальные'
END
""".strip()


def _clean(value: str) -> str:
    return textwrap.dedent(value).strip()


def _table_pattern(table: str) -> str:
    return rf"(?:dbo\.)?{re.escape(table.rsplit('.', 1)[-1])}"


def _fact(
    key: str,
    *labels: str,
    tolerance: str | None = None,
    tool_required: bool = True,
    answer_required: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "golden_key": key,
        "labels": list(labels),
    }
    if tolerance is not None:
        result["tolerance"] = tolerance
    if not tool_required:
        result["tool_required"] = False
    if not answer_required:
        result["answer_required"] = False
    return result


def _page_context(tab: str) -> dict[str, Any]:
    return {
        "pathname": f"/superset/dashboard/{DASHBOARD_ID}/",
        "pageType": "dashboard",
        "dashboardContext": {
            "id": DASHBOARD_ID,
            "title": "IVR детальная информация",
            "activeTabLabel": tab,
            "charts": [
                {"id": chart_id, "title": title}
                for chart_id, title in [*GLOBAL_CHARTS, *TAB_CHARTS[tab]]
            ],
            "activeFilters": [
                {
                    "name": "Фильтр по времени",
                    "filterType": "filter_time",
                    "value": (
                        f"{START}T00:00:00 : {END}T00:00:00"
                    ),
                }
            ],
        },
    }


def _case(
    case_id: str,
    tab: str,
    prompt: str,
    table: str,
    golden_sql: str,
    facts: list[dict[str, Any]],
    patterns: list[str],
    chart_ids: list[int],
    family: str,
    *,
    answer_must_not_match: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "id": case_id,
        "category": "business_answer",
        "profile": "analyst",
        "prompt": prompt,
        "page_context": _page_context(tab),
        "database_id": DATABASE_ID,
        "golden_sql": _clean(golden_sql),
        "facts": facts,
        "sql_must_match": [
            _table_pattern(table),
            re.escape(START),
            re.escape(END),
            *patterns,
        ],
        "required_tools": ["execute_sql"],
        "preferred_tools": [
            "get_dashboard_context",
            "get_chart_context",
            "get_schema",
        ],
        "source_dashboard_id": DASHBOARD_ID,
        "source_chart_ids": chart_ids,
        "tags": [
            "release-v3",
            "dashboard-grounded",
            "call-center",
            tab.casefold().replace(" ", "-"),
            family,
        ],
    }
    if answer_must_not_match:
        result["answer_must_not_match"] = answer_must_not_match
    return result


def _month_cases() -> list[dict[str, Any]]:
    return [
        _case(
            "dashboard_peak_unique_calls",
            "За месяц",
            (
                "На общем графике «Количества звонков» в какой день выбранного "
                "периода был максимум и сколько звонков там показано?"
            ),
            CALLS,
            f"""
            WITH daily AS (
              SELECT CAST(date_call AS date) AS call_date,
                     COUNT(DISTINCT id) AS unique_calls
              FROM {CALLS}
              WHERE date_call >= '{START}' AND date_call < '{END}'
              GROUP BY CAST(date_call AS date)
            )
            SELECT TOP 1 CONVERT(char(10), call_date, 23) AS peak_date,
                   unique_calls
            FROM daily
            ORDER BY unique_calls DESC, call_date ASC
            """,
            [
                _fact("peak_date", "дат", "день"),
                _fact("unique_calls", "звон", "колич"),
            ],
            [r"count\s*\(\s*distinct\s+id\s*\)", r"date_call"],
            [269],
            "unique-call-volume",
        ),
        _case(
            "month_automation_level",
            "За месяц",
            (
                "Какой у нас общий уровень автоматизации за выбранный период: "
                "сколько обращений автоматизировано из общего числа и какой это процент?"
            ),
            SUMMARY,
            f"""
            SELECT
              SUM(CASE WHEN automation = 1 THEN 1 ELSE 0 END) AS automated_calls,
              COUNT(*) AS total_calls,
              CAST(100.0 * SUM(CASE WHEN automation = 1 THEN 1 ELSE 0 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS automation_pct
            FROM {SUMMARY}
            WHERE date_call >= '{START}' AND date_call < '{END}'
            """,
            [
                _fact("automated_calls", "автомат"),
                _fact("total_calls", "всего", "общ"),
                _fact("automation_pct", "уров", "процент", tolerance="0.1"),
            ],
            [r"automation", r"100(?:\.0)?"],
            [276],
            "automation-level",
        ),
        _case(
            "month_peak_automation_day",
            "За месяц",
            (
                "В какой день выбранного периода автоматизация была самой высокой? "
                "Назови дату, процент и количество автоматизированных обращений в этот день."
            ),
            SUMMARY,
            f"""
            WITH daily AS (
              SELECT CAST(date_call AS date) AS call_date,
                     SUM(CASE WHEN automation = 1 THEN 1 ELSE 0 END) AS automated_calls,
                     COUNT(*) AS total_calls,
                     CAST(100.0 * SUM(CASE WHEN automation = 1 THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS automation_pct
              FROM {SUMMARY}
              WHERE date_call >= '{START}' AND date_call < '{END}'
              GROUP BY CAST(date_call AS date)
            )
            SELECT TOP 1 CONVERT(char(10), call_date, 23) AS peak_date,
                   automated_calls, total_calls, automation_pct
            FROM daily
            ORDER BY automation_pct DESC, call_date ASC
            """,
            [
                _fact("peak_date", "дат", "день"),
                _fact("automated_calls", "автомат"),
                _fact("automation_pct", "процент", "уров", tolerance="0.1"),
            ],
            [r"group\s+by", r"order\s+by", r"automation"],
            [276],
            "automation-peak",
        ),
        _case(
            "month_top_completion_path",
            "За месяц",
            (
                "Каким способом чаще всего завершались клиентские обращения за "
                "выбранный период? Дай название пути, количество и долю."
            ),
            SUMMARY,
            f"""
            WITH paths AS (
              SELECT definition, COUNT(*) AS path_calls
              FROM {SUMMARY}
              WHERE date_call >= '{START}' AND date_call < '{END}'
                AND definition IS NOT NULL
              GROUP BY definition
            ), ranked AS (
              SELECT definition, path_calls,
                     SUM(path_calls) OVER () AS total_calls,
                     ROW_NUMBER() OVER (ORDER BY path_calls DESC, definition ASC) AS rn
              FROM paths
            )
            SELECT definition AS top_path, path_calls,
                   CAST(100.0 * path_calls / NULLIF(total_calls, 0)
                        AS decimal(18, 2)) AS path_pct
            FROM ranked WHERE rn = 1
            """,
            [
                _fact("top_path", "путь", "способ"),
                _fact("path_calls", "колич", "обращ"),
                _fact(
                    "path_pct",
                    "дол",
                    "процент",
                    tolerance="0.1",
                    tool_required=False,
                ),
            ],
            [r"definition", r"group\s+by", r"order\s+by"],
            [275],
            "completion-path",
        ),
        _case(
            "month_manager_vs_automated",
            "За месяц",
            (
                "Сколько обращений за выбранный период затронули менеджера, а "
                "сколько были автоматизированы? Эти группы могут пересекаться — просто сравни их."
            ),
            SUMMARY,
            f"""
            SELECT
              SUM(CASE WHEN mng_sign = 1 THEN 1 ELSE 0 END) AS manager_calls,
              SUM(CASE WHEN automation = 1 THEN 1 ELSE 0 END) AS automated_calls,
              COUNT(*) AS total_calls
            FROM {SUMMARY}
            WHERE date_call >= '{START}' AND date_call < '{END}'
            """,
            [
                _fact("manager_calls", "менедж"),
                _fact("automated_calls", "автомат"),
                _fact(
                    "total_calls",
                    "всего",
                    "общ",
                    answer_required=False,
                ),
            ],
            [r"mng_sign", r"automation"],
            [275, 276],
            "manager-vs-automation",
            answer_must_not_match=[
                r"дел\w*\s+на\s+(?:два\s+)?непересекающ\w*\s+кластер",
                r"(?:по\s+определению|всегда)\s+не\s+пересека",
            ],
        ),
        _case(
            "month_button_channel_shares",
            "За месяц",
            (
                "Как распределились нажатия за выбранный период между роботом, "
                "обычным IVR и Custom IVR? Покажи три доли в процентах."
            ),
            SUMMARY,
            f"""
            SELECT
              CAST(100.0 * SUM(robot_sign) / NULLIF(COUNT(*), 0)
                   AS decimal(18, 2)) AS robot_pct,
              CAST(100.0 * SUM(ivr_sign) / NULLIF(COUNT(*), 0)
                   AS decimal(18, 2)) AS ivr_pct,
              CAST(100.0 * SUM(custom_sign) / NULLIF(COUNT(*), 0)
                   AS decimal(18, 2)) AS custom_pct
            FROM {SUMMARY}
            WHERE date_call >= '{START}' AND date_call < '{END}'
            """,
            [
                _fact("robot_pct", "робот", tolerance="0.1"),
                _fact("ivr_pct", "ivr", tolerance="0.1"),
                _fact("custom_pct", "custom", "кастом", tolerance="0.1"),
            ],
            [r"robot_sign", r"ivr_sign", r"custom_sign"],
            [277],
            "button-shares",
        ),
        _case(
            "month_automation_tool_shares",
            "За месяц",
            (
                "Разложи автоматизацию за выбранный период по инструментам: "
                "робот, IVR, Custom IVR и смешанный маршрут. Нужны четыре процента."
            ),
            SUMMARY,
            f"""
            SELECT
              CAST(100.0 * SUM(CASE WHEN ivr = 'robot' THEN 1 ELSE 0 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS robot_pct,
              CAST(100.0 * SUM(CASE WHEN ivr = 'ivr' THEN 1 ELSE 0 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS ivr_pct,
              CAST(100.0 * SUM(CASE WHEN ivr = 'custom' THEN 1 ELSE 0 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS custom_pct,
              CAST(100.0 * SUM(CASE WHEN automation = 1
                                     AND ivr NOT IN ('robot', 'ivr', 'custom')
                                    THEN 1 ELSE 0 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS mixed_pct
            FROM {SUMMARY}
            WHERE date_call >= '{START}' AND date_call < '{END}'
            """,
            [
                _fact("robot_pct", "робот", tolerance="0.1"),
                _fact("ivr_pct", "ivr", tolerance="0.1"),
                _fact("custom_pct", "custom", "кастом", tolerance="0.1"),
                _fact("mixed_pct", "смеш", "mixed", tolerance="0.1"),
            ],
            [r"ivr\s*=\s*'robot'", r"ivr\s*=\s*'ivr'", r"automation"],
            [278],
            "automation-tools",
        ),
        _case(
            "month_manager_repeat_rate",
            "За месяц",
            (
                "Какова доля повторных обращений после менеджера за выбранный "
                "период? Покажи повторные, все обращения к менеджеру и процент."
            ),
            SUMMARY,
            f"""
            SELECT
              COUNT(CASE WHEN ivr = 'mng' AND mng_calls = 2 THEN phone END)
                AS repeat_calls,
              COUNT(CASE WHEN ivr = 'mng' THEN phone END) AS manager_calls,
              CAST(100.0 * COUNT(CASE WHEN ivr = 'mng' AND mng_calls = 2 THEN phone END)
                   / NULLIF(COUNT(CASE WHEN ivr = 'mng' THEN phone END), 0)
                   AS decimal(18, 2)) AS repeat_pct
            FROM {SUMMARY}
            WHERE date_call >= '{START}' AND date_call < '{END}'
            """,
            [
                _fact("repeat_calls", "повтор", "перезвон"),
                _fact("manager_calls", "менедж", "всего"),
                _fact("repeat_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"mng_calls\s*=\s*2", r"ivr\s*=\s*'mng'"],
            [268],
            "manager-repeats",
        ),
        _case(
            "month_peak_manager_repeat_day",
            "За месяц",
            (
                "В какой день выбранного периода доля повторных обращений после "
                "менеджера была максимальной? Дай дату, количество повторных и процент."
            ),
            SUMMARY,
            f"""
            WITH daily AS (
              SELECT CAST(date_call AS date) AS call_date,
                     COUNT(CASE WHEN mng_calls = 2 THEN phone END) AS repeat_calls,
                     COUNT(phone) AS manager_calls,
                     CAST(100.0 * COUNT(CASE WHEN mng_calls = 2 THEN phone END)
                          / NULLIF(COUNT(phone), 0) AS decimal(18, 2)) AS repeat_pct
              FROM {SUMMARY}
              WHERE date_call >= '{START}' AND date_call < '{END}' AND ivr = 'mng'
              GROUP BY CAST(date_call AS date)
            )
            SELECT TOP 1 CONVERT(char(10), call_date, 23) AS peak_date,
                   repeat_calls, manager_calls, repeat_pct
            FROM daily
            ORDER BY repeat_pct DESC, call_date ASC
            """,
            [
                _fact("peak_date", "дат", "день"),
                _fact("repeat_calls", "повтор", "перезвон"),
                _fact("repeat_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"mng_calls\s*=\s*2", r"ivr\s*=\s*'mng'", r"group\s+by"],
            [268],
            "manager-repeat-peak",
        ),
    ]


def _ivr_cases() -> list[dict[str, Any]]:
    return [
        _case(
            "ivr_top_menu_choice",
            "IVR",
            (
                "Какой пункт меню IVR выбирали чаще всего за выбранный период? "
                "Назови его понятным названием и количество выборов."
            ),
            CALLS,
            f"""
            WITH choices AS (
              SELECT {IVR_CHOICE_LABEL} AS choice_name, COUNT(id) AS choice_count
              FROM {CALLS}
              WHERE created_at >= '{START}' AND created_at < '{END}'
                AND ivr_choice IN ({IVR_CHOICES})
              GROUP BY {IVR_CHOICE_LABEL}
            )
            SELECT TOP 1 choice_name AS top_choice, choice_count
            FROM choices
            ORDER BY choice_count DESC, choice_name ASC
            """,
            [
                _fact("top_choice", "пункт", "выбор", "назван"),
                _fact("choice_count", "колич", "выбор"),
            ],
            [r"ivr_choice", r"group\s+by", r"order\s+by"],
            [272],
            "menu-top",
        ),
        _case(
            "ivr_language_split",
            "IVR",
            (
                "Сколько выборов на казахском и сколько на русском было среди "
                "пунктов, показанных на графике за выбранный период?"
            ),
            CALLS,
            f"""
            SELECT
              SUM(CASE WHEN ivr_choice LIKE 'kz[_]%' THEN 1 ELSE 0 END) AS kz_choices,
              SUM(CASE WHEN ivr_choice LIKE 'ru[_]%' THEN 1 ELSE 0 END) AS ru_choices,
              COUNT(*) AS total_choices
            FROM {CALLS}
            WHERE created_at >= '{START}' AND created_at < '{END}'
              AND ivr_choice IN ({IVR_CHOICES})
            """,
            [
                _fact("kz_choices", "казах", "kz"),
                _fact("ru_choices", "рус", "ru"),
                _fact(
                    "total_choices",
                    "всего",
                    "итого",
                    tool_required=False,
                    answer_required=False,
                ),
            ],
            [r"ivr_choice", r"kz", r"ru"],
            [272],
            "language-split",
        ),
        _case(
            "ivr_policy_download_split",
            "IVR",
            (
                "Сколько раз клиенты выбирали скачивание полиса на казахском и "
                "на русском за выбранный период? Покажи оба значения и итог."
            ),
            CALLS,
            f"""
            SELECT
              SUM(CASE WHEN ivr_choice = 'kz_polis_get' THEN 1 ELSE 0 END)
                AS kz_downloads,
              SUM(CASE WHEN ivr_choice = 'ru_polis_get' THEN 1 ELSE 0 END)
                AS ru_downloads,
              SUM(CASE WHEN ivr_choice IN ('kz_polis_get', 'ru_polis_get')
                       THEN 1 ELSE 0 END) AS total_downloads
            FROM {CALLS}
            WHERE created_at >= '{START}' AND created_at < '{END}'
            """,
            [
                _fact("kz_downloads", "казах", "kz"),
                _fact("ru_downloads", "рус", "ru", tool_required=False),
                _fact("total_downloads", "всего", "итого"),
            ],
            [r"kz_polis_get", r"ru_polis_get"],
            [272],
            "policy-download",
        ),
        _case(
            "ivr_status_vs_application",
            "IVR",
            (
                "Что выбирали чаще за выбранный период: проверку статуса заявки "
                "или инструкцию по заявке? Сложи оба языка и покажи разницу."
            ),
            CALLS,
            f"""
            SELECT
              SUM(CASE WHEN ivr_choice IN ('kz_cc_status', 'ru_cc_status')
                       THEN 1 ELSE 0 END) AS status_choices,
              SUM(CASE WHEN ivr_choice IN ('kz_cc_application', 'ru_cc_application')
                       THEN 1 ELSE 0 END) AS application_choices,
              ABS(
                SUM(CASE WHEN ivr_choice IN ('kz_cc_status', 'ru_cc_status')
                         THEN 1 ELSE 0 END)
                - SUM(CASE WHEN ivr_choice IN (
                      'kz_cc_application', 'ru_cc_application'
                    ) THEN 1 ELSE 0 END)
              ) AS difference
            FROM {CALLS}
            WHERE created_at >= '{START}' AND created_at < '{END}'
            """,
            [
                _fact("status_choices", "статус"),
                _fact("application_choices", "инструкц", "заявк"),
                _fact("difference", "разниц", tool_required=False),
            ],
            [r"cc_status", r"cc_application"],
            [272],
            "status-vs-application",
        ),
        _case(
            "ivr_phone_capture_counts",
            "IVR",
            (
                "По IVR-сценариям на этой вкладке сколько номеров удалось "
                "записать, а сколько не удалось за выбранный период?"
            ),
            CALLS,
            f"""
            SELECT
              SUM(CASE WHEN whatsapp_collected_value IS NULL
                            OR whatsapp_collected_value = 'null'
                       THEN 1 ELSE 0 END) AS missed_numbers,
              SUM(CASE WHEN whatsapp_collected_value IS NOT NULL
                            AND whatsapp_collected_value <> 'null'
                       THEN 1 ELSE 0 END) AS captured_numbers,
              COUNT(*) AS total_attempts
            FROM {CALLS}
            WHERE created_at >= '{START}' AND created_at < '{END}'
              AND ivr_choice IN ({IVR_CHOICES})
            """,
            [
                _fact("missed_numbers", "не удалось", "не запис"),
                _fact("captured_numbers", "удалось", "записан"),
            ],
            [r"whatsapp_collected_value", r"ivr_choice"],
            [273],
            "phone-capture-counts",
        ),
        _case(
            "ivr_phone_capture_rate",
            "IVR",
            (
                "Какой процент номеров удалось записать в IVR-сценариях на этой "
                "вкладке за выбранный период? Дай успешные, все попытки и долю."
            ),
            CALLS,
            f"""
            SELECT
              SUM(CASE WHEN whatsapp_collected_value IS NOT NULL
                            AND whatsapp_collected_value <> 'null'
                       THEN 1 ELSE 0 END) AS captured_numbers,
              COUNT(*) AS total_attempts,
              CAST(100.0 * SUM(CASE WHEN whatsapp_collected_value IS NOT NULL
                                         AND whatsapp_collected_value <> 'null'
                                    THEN 1 ELSE 0 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS capture_pct
            FROM {CALLS}
            WHERE created_at >= '{START}' AND created_at < '{END}'
              AND ivr_choice IN ({IVR_CHOICES})
            """,
            [
                _fact("captured_numbers", "успеш", "записан"),
                _fact("total_attempts", "всего", "попыт"),
                _fact("capture_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"whatsapp_collected_value", r"ivr_choice", r"100(?:\.0)?"],
            [273],
            "phone-capture-rate",
        ),
        _case(
            "ivr_worst_phone_capture_day",
            "IVR",
            (
                "В какой день выбранного периода было больше всего не записанных "
                "номеров в IVR? Назови дату, число неудач и их долю за этот день."
            ),
            CALLS,
            f"""
            WITH daily AS (
              SELECT CAST(created_at AS date) AS call_date,
                     SUM(CASE WHEN whatsapp_collected_value IS NULL
                                   OR whatsapp_collected_value = 'null'
                              THEN 1 ELSE 0 END) AS missed_numbers,
                     COUNT(*) AS total_attempts
              FROM {CALLS}
              WHERE created_at >= '{START}' AND created_at < '{END}'
                AND ivr_choice IN ({IVR_CHOICES})
              GROUP BY CAST(created_at AS date)
            )
            SELECT TOP 1 CONVERT(char(10), call_date, 23) AS worst_date,
                   missed_numbers, total_attempts,
                   CAST(100.0 * missed_numbers / NULLIF(total_attempts, 0)
                        AS decimal(18, 2)) AS missed_pct
            FROM daily
            ORDER BY missed_numbers DESC, call_date ASC
            """,
            [
                _fact("worst_date", "дат", "день"),
                _fact("missed_numbers", "неудач", "не запис"),
                _fact(
                    "missed_pct",
                    "дол",
                    "процент",
                    tolerance="0.1",
                    tool_required=False,
                ),
            ],
            [r"whatsapp_collected_value", r"group\s+by", r"order\s+by"],
            [273],
            "phone-capture-worst-day",
        ),
    ]


def _robot_cases() -> list[dict[str, Any]]:
    qualified = (
        f"((start_date >= '{CUTOFF}' AND problem < 0.25) "
        f"OR (start_date < '{CUTOFF}' AND problem < 0.7))"
    )
    self_service = """
    response IN (
      'custom/buy_policy', 'custom/update_tech_passport_data',
      'custom/add_or_replace_vehicle', 'custom/add_or_replace_driver',
      'custom/pereoformit', 'custom/calculate_price', 'custom/raschitat',
      'custom/terminate_policy', 'custom/rastor_polis', 'custom/renew_policy',
      'custom/prodlit_polis', 'custom/download_or_check_policy',
      'custom/skachat_polis'
    )
    """.strip()
    return [
        _case(
            "robot_route_outcomes",
            "Робот",
            (
                "Среди звонков роботизированной ветки за выбранный период сколько "
                "завершились в роботе, сколько ушли напрямую менеджеру и сколько попали в другие классы?"
            ),
            CALLS,
            f"""
            SELECT
              SUM(CASE WHEN source = 'rbt' THEN 1 ELSE 0 END) AS robot_calls,
              SUM(CASE WHEN source = 'rbt_dir_mng' THEN 1 ELSE 0 END)
                AS direct_manager_calls,
              SUM(CASE WHEN source NOT IN ('rbt', 'rbt_dir_mng') OR source IS NULL
                       THEN 1 ELSE 0 END) AS other_calls,
              COUNT(*) AS total_calls
            FROM {CALLS}
            WHERE date_call >= '{START}' AND date_call < '{END}' AND ivr = '2:1:r'
            """,
            [
                _fact("robot_calls", "робот"),
                _fact("direct_manager_calls", "менедж"),
                _fact("other_calls", "друг", "остальн"),
                _fact(
                    "total_calls",
                    "всего",
                    "итого",
                    answer_required=False,
                ),
            ],
            [r"ivr\s*=\s*'2:1:r'", r"rbt_dir_mng", r"source"],
            [270, 279, 280],
            "route-outcomes",
        ),
        _case(
            "robot_top_source",
            "Робот",
            (
                "Какой класс завершения был самым частым в роботизированной ветке за "
                "выбранный период? Покажи класс, количество и долю."
            ),
            CALLS,
            f"""
            WITH sources AS (
              SELECT source, COUNT(*) AS source_calls
              FROM {CALLS}
              WHERE date_call >= '{START}' AND date_call < '{END}' AND ivr = '2:1:r'
              GROUP BY source
            ), ranked AS (
              SELECT source, source_calls, SUM(source_calls) OVER () AS total_calls,
                     ROW_NUMBER() OVER (ORDER BY source_calls DESC, source ASC) AS rn
              FROM sources
            )
            SELECT source AS top_source, source_calls,
                   CAST(100.0 * source_calls / NULLIF(total_calls, 0)
                        AS decimal(18, 2)) AS source_pct
            FROM ranked WHERE rn = 1
            """,
            [
                _fact("top_source", "класс", "source"),
                _fact("source_calls", "колич", "звон"),
                _fact("source_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"ivr\s*=\s*'2:1:r'", r"group\s+by", r"source"],
            [270, 279, 280],
            "top-source",
        ),
        _case(
            "robot_peak_day",
            "Робот",
            (
                "В какой день выбранного периода было больше всего звонков в "
                "роботизированной ветке и сколько их было?"
            ),
            CALLS,
            f"""
            SELECT TOP 1 CONVERT(char(10), CAST(date_call AS date), 23) AS peak_date,
                   COUNT(*) AS robot_branch_calls
            FROM {CALLS}
            WHERE date_call >= '{START}' AND date_call < '{END}' AND ivr = '2:1:r'
            GROUP BY CAST(date_call AS date)
            ORDER BY robot_branch_calls DESC, CAST(date_call AS date) ASC
            """,
            [
                _fact("peak_date", "дат", "день"),
                _fact("robot_branch_calls", "звон", "колич"),
            ],
            [r"ivr\s*=\s*'2:1:r'", r"group\s+by", r"order\s+by"],
            [270, 279, 280],
            "peak-day",
        ),
        _case(
            "robot_confidence_rate",
            "Робот",
            (
                "Какова доля уверенных классификаций робота за выбранный период? "
                "Покажи уверенные, все классификации и процент."
            ),
            ROBOT,
            f"""
            SELECT
              COUNT(CASE WHEN (start_date >= '{CUTOFF}' AND score >= 0.6)
                               OR (start_date < '{CUTOFF}' AND score >= 0.7)
                         THEN 1 END) AS confident_calls,
              COUNT(*) AS total_calls,
              CAST(100.0 * COUNT(CASE
                    WHEN (start_date >= '{CUTOFF}' AND score >= 0.6)
                      OR (start_date < '{CUTOFF}' AND score >= 0.7) THEN 1 END)
                   / NULLIF(COUNT(*), 0) AS decimal(18, 2)) AS confidence_pct
            FROM {ROBOT}
            WHERE created_date >= '{START}' AND created_date < '{END}'
            """,
            [
                _fact("confident_calls", "уверенн"),
                _fact("total_calls", "всего", "классификац"),
                _fact("confidence_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"score\s*>=\s*0\.6"],
            [271],
            "confidence-rate",
        ),
        _case(
            "robot_lowest_confidence_day",
            "Робот",
            (
                "В какой день выбранного периода уверенность классификации была "
                "самой низкой? Назови дату, уверенные классификации и процент."
            ),
            ROBOT,
            f"""
            WITH daily AS (
              SELECT CAST(created_date AS date) AS call_date,
                     COUNT(CASE WHEN (start_date >= '{CUTOFF}' AND score >= 0.6)
                                      OR (start_date < '{CUTOFF}' AND score >= 0.7)
                                THEN 1 END) AS confident_calls,
                     COUNT(*) AS total_calls
              FROM {ROBOT}
              WHERE created_date >= '{START}' AND created_date < '{END}'
              GROUP BY CAST(created_date AS date)
            )
            SELECT TOP 1 CONVERT(char(10), call_date, 23) AS lowest_date,
                   confident_calls, total_calls,
                   CAST(100.0 * confident_calls / NULLIF(total_calls, 0)
                        AS decimal(18, 2)) AS confidence_pct
            FROM daily
            ORDER BY confidence_pct ASC, call_date ASC
            """,
            [
                _fact("lowest_date", "дат", "день"),
                _fact("confident_calls", "уверенн"),
                _fact("confidence_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"score\s*>=\s*0\.6", r"group\s+by", r"order\s+by"],
            [271],
            "confidence-low-day",
        ),
        _case(
            "robot_top_response_class",
            "Робот",
            (
                "На графике распределения какой класс ответа робота был самым "
                "частым за выбранный период? Покажи класс, количество и долю."
            ),
            ROBOT,
            f"""
            WITH classes AS (
              SELECT {ROBOT_CLASS} AS response_class, COUNT(*) AS class_calls
              FROM {ROBOT}
              WHERE start_date >= '{START}' AND start_date < '{END}' AND {qualified}
              GROUP BY {ROBOT_CLASS}
            ), ranked AS (
              SELECT response_class, class_calls, SUM(class_calls) OVER () AS total_calls,
                     ROW_NUMBER() OVER (
                       ORDER BY class_calls DESC, response_class ASC
                     ) AS rn
              FROM classes
            )
            SELECT response_class AS top_class, class_calls,
                   CAST(100.0 * class_calls / NULLIF(total_calls, 0)
                        AS decimal(18, 2)) AS class_pct
            FROM ranked WHERE rn = 1
            """,
            [
                _fact("top_class", "класс", "ответ", tool_required=False),
                _fact("class_calls", "колич", "звон"),
                _fact(
                    "class_pct",
                    "дол",
                    "процент",
                    tolerance="0.1",
                    tool_required=False,
                ),
            ],
            [r"response", r"problem\s*<\s*0\.25"],
            [274],
            "top-response-class",
        ),
        _case(
            "robot_manager_vs_self_service",
            "Робот",
            (
                "Среди звонков, попавших в распределение классов за выбранный период, "
                "сколько ушло менеджеру и сколько попало в конкретные сценарии самообслуживания?"
            ),
            ROBOT,
            f"""
            SELECT
              SUM(CASE WHEN response = 'custom/manager' THEN 1 ELSE 0 END)
                AS manager_calls,
              SUM(CASE WHEN {self_service} THEN 1 ELSE 0 END) AS self_service_calls,
              COUNT(*) AS qualified_calls
            FROM {ROBOT}
            WHERE start_date >= '{START}' AND start_date < '{END}' AND {qualified}
            """,
            [
                _fact("manager_calls", "менедж"),
                _fact("self_service_calls", "самообслуж", "сценари"),
                _fact(
                    "qualified_calls",
                    "всего",
                    "распознан",
                    answer_required=False,
                ),
            ],
            [r"custom/manager", r"custom/buy_policy", r"problem\s*<\s*0\.25"],
            [274],
            "manager-vs-self-service",
        ),
        _case(
            "robot_business_intent_breakdown",
            "Робот",
            (
                "Разбей обращения из распределения классов за выбранный период "
                "по основным целям: новый полис, переоформление, расчёт стоимости, "
                "расторжение, продление и скачивание полиса."
            ),
            ROBOT,
            f"""
            SELECT
              SUM(CASE WHEN response = 'custom/buy_policy' THEN 1 ELSE 0 END)
                AS new_policy_calls,
              SUM(CASE WHEN response IN (
                    'custom/update_tech_passport_data', 'custom/add_or_replace_vehicle',
                    'custom/add_or_replace_driver', 'custom/pereoformit'
                  ) THEN 1 ELSE 0 END) AS reissue_calls,
              SUM(CASE WHEN response IN ('custom/calculate_price', 'custom/raschitat')
                       THEN 1 ELSE 0 END) AS price_calls,
              SUM(CASE WHEN response IN ('custom/terminate_policy', 'custom/rastor_polis')
                       THEN 1 ELSE 0 END) AS terminate_calls,
              SUM(CASE WHEN response IN ('custom/renew_policy', 'custom/prodlit_polis')
                       THEN 1 ELSE 0 END) AS renew_calls,
              SUM(CASE WHEN response IN (
                    'custom/download_or_check_policy', 'custom/skachat_polis'
                  ) THEN 1 ELSE 0 END) AS download_calls
            FROM {ROBOT}
            WHERE start_date >= '{START}' AND start_date < '{END}' AND {qualified}
            """,
            [
                _fact("new_policy_calls", "нов", "полис"),
                _fact("reissue_calls", "переоформ"),
                _fact("price_calls", "расч", "стоимост"),
                _fact("terminate_calls", "расторг"),
                _fact("renew_calls", "продл"),
                _fact("download_calls", "скач"),
            ],
            [r"custom/buy_policy", r"custom/pereoformit", r"custom/skachat_polis"],
            [274],
            "business-intents",
        ),
        _case(
            "robot_spam_share",
            "Робот",
            (
                "Сколько звонков из распределения классов за выбранный период "
                "попали в спам и какую долю от всех таких звонков это составляет?"
            ),
            ROBOT,
            f"""
            SELECT
              SUM(CASE WHEN response IN (
                    'custom/allo', 'custom/da', 'custom/net', 'custom/no',
                    'custom/yes', 'custom/no_thanks', 'custom/mat',
                    'custom/try_before_manager'
                  ) THEN 1 ELSE 0 END) AS spam_calls,
              COUNT(*) AS qualified_calls,
              CAST(100.0 * SUM(CASE WHEN response IN (
                    'custom/allo', 'custom/da', 'custom/net', 'custom/no',
                    'custom/yes', 'custom/no_thanks', 'custom/mat',
                    'custom/try_before_manager'
                  ) THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
                   AS decimal(18, 2)) AS spam_pct
            FROM {ROBOT}
            WHERE start_date >= '{START}' AND start_date < '{END}' AND {qualified}
            """,
            [
                _fact("spam_calls", "спам"),
                _fact(
                    "qualified_calls",
                    "всего",
                    "распознан",
                    answer_required=False,
                ),
                _fact("spam_pct", "дол", "процент", tolerance="0.1"),
            ],
            [r"custom/allo", r"custom/try_before_manager", r"problem\s*<\s*0\.25"],
            [274],
            "spam-share",
        ),
    ]


def _cross_tab_case() -> dict[str, Any]:
    case = _case(
        "dashboard_shift_briefing",
        "За месяц",
        (
            "Перед сменой дай короткую сводку по этому дэшборду за выбранный "
            "период: общий уровень автоматизации, долю успешно записанных номеров "
            "в IVR и долю уверенных классификаций робота."
        ),
        SUMMARY,
        f"""
        SELECT
          (SELECT CAST(100.0 * SUM(CASE WHEN automation = 1 THEN 1 ELSE 0 END)
                       / NULLIF(COUNT(*), 0) AS decimal(18, 2))
           FROM {SUMMARY}
           WHERE date_call >= '{START}' AND date_call < '{END}') AS automation_pct,
          (SELECT CAST(100.0 * SUM(CASE
                         WHEN whatsapp_collected_value IS NOT NULL
                          AND whatsapp_collected_value <> 'null' THEN 1 ELSE 0 END)
                       / NULLIF(COUNT(*), 0) AS decimal(18, 2))
           FROM {CALLS}
           WHERE created_at >= '{START}' AND created_at < '{END}'
             AND ivr_choice IN ({IVR_CHOICES})) AS capture_pct,
          (SELECT CAST(100.0 * COUNT(CASE
                         WHEN (start_date >= '{CUTOFF}' AND score >= 0.6)
                           OR (start_date < '{CUTOFF}' AND score >= 0.7)
                         THEN 1 END) / NULLIF(COUNT(*), 0) AS decimal(18, 2))
           FROM {ROBOT}
           WHERE created_date >= '{START}' AND created_date < '{END}') AS confidence_pct
        """,
        [
            _fact("automation_pct", "автомат", tolerance="0.1"),
            _fact("capture_pct", "запис", "ivr", tolerance="0.1"),
            _fact("confidence_pct", "уверенн", "робот", tolerance="0.1"),
        ],
        [
            _table_pattern(CALLS),
            _table_pattern(ROBOT),
            r"automation",
            r"whatsapp_collected_value",
            r"score\s*>=\s*0\.6",
        ],
        [276, 273, 271],
        "shift-briefing",
    )
    case["allow_multiple_sql_calls"] = True
    return case


def build_document() -> dict[str, Any]:
    cases = [*_month_cases(), *_ivr_cases(), *_robot_cases(), _cross_tab_case()]
    ids = [case["id"] for case in cases]
    assert len(cases) == 26
    assert len(ids) == len(set(ids))
    assert all(case["required_tools"] == ["execute_sql"] for case in cases)
    assert all(
        case["page_context"]["dashboardContext"]["id"] == DASHBOARD_ID
        for case in cases
    )
    return {
        "benchmark_version": "3.1.0",
        "dataset_name": "superset-native-ai-dashboard-44-v1",
        "description": (
            "26 dashboard-grounded call-center questions over dashboard 44; "
            "every case requires executed SQL and a fact-grounded answer."
        ),
        "expected_repeats": 3,
        "source_dashboard_id": DASHBOARD_ID,
        "frozen_period": {"start_inclusive": START, "end_exclusive": END},
        "cases": cases,
    }


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("cases_release.json"),
    )
    parser.add_argument(
        "--seed-output",
        type=Path,
        default=Path(__file__).with_name("cases.json"),
    )
    parser.add_argument("--goldens-from-report", type=Path)
    parser.add_argument(
        "--goldens-output",
        type=Path,
        default=Path(__file__).with_name("goldens_release.json"),
    )
    args = parser.parse_args()

    document = build_document()
    _write(args.output, document)
    seed_ids = {
        "month_automation_level",
        "ivr_phone_capture_rate",
        "robot_confidence_rate",
        "dashboard_shift_briefing",
    }
    _write(
        args.seed_output,
        {
            **document,
            "dataset_name": document["dataset_name"] + "-smoke",
            "description": "Four-case smoke subset of the dashboard-44 benchmark.",
            "expected_repeats": 1,
            "cases": [case for case in document["cases"] if case["id"] in seed_ids],
        },
    )
    print(args.output)
    print(args.seed_output)

    if args.goldens_from_report:
        report = json.loads(args.goldens_from_report.read_text(encoding="utf-8"))
        results = {item.get("case_id"): item for item in report.get("results", [])}
        golden_rows = {
            case["id"]: (results.get(case["id"], {}).get("sql_evidence") or {}).get(
                "golden_rows"
            )
            for case in document["cases"]
        }
        invalid = [
            case_id
            for case_id, rows in golden_rows.items()
            if not isinstance(rows, list) or len(rows) != 1
        ]
        if invalid:
            raise ValueError(f"Report lacks one-row goldens for: {invalid}")
        _write(
            args.goldens_output,
            {
                "benchmark_version": document["benchmark_version"],
                "source_run": report.get("run_name"),
                "source_started_at": report.get("started_at"),
                "golden_rows": golden_rows,
            },
        )
        print(args.goldens_output)


if __name__ == "__main__":
    main()
