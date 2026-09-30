import importlib.util
import io
import re
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError


SPEC = importlib.util.spec_from_file_location(
    "superset_ai_benchmark", Path(__file__).with_name("run.py")
)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(benchmark)

GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "superset_ai_benchmark_generator", Path(__file__).with_name("generate_cases.py")
)
generator = importlib.util.module_from_spec(GENERATOR_SPEC)
assert GENERATOR_SPEC.loader is not None
GENERATOR_SPEC.loader.exec_module(generator)

AGGREGATE_SPEC = importlib.util.spec_from_file_location(
    "superset_ai_benchmark_aggregate", Path(__file__).with_name("aggregate.py")
)
aggregate = importlib.util.module_from_spec(AGGREGATE_SPEC)
assert AGGREGATE_SPEC.loader is not None
AGGREGATE_SPEC.loader.exec_module(aggregate)


class BenchmarkSelfCheck(unittest.TestCase):
    def test_langfuse_score_readback_uses_exact_deterministic_id(self):
        trace_id = "trace-1"
        score_name = "bench.overall"
        score_id = benchmark._langfuse_score_id(trace_id, score_name)

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"id": score_id, "traceId": trace_id, "name": score_name}

        class Transport:
            def request(self, path, method):
                self.path = path
                self.method = method
                return Response()

        bridge = benchmark.LangfuseBridge.__new__(benchmark.LangfuseBridge)
        bridge._transport = Transport()
        bridge._wait_for_scores(trace_id, {score_name})
        self.assertEqual(
            bridge._transport.path, f"api/public/scores/{score_id}"
        )
        self.assertEqual(bridge._transport.method, "GET")

    def test_database_load_gate_requires_stable_idle_after_busy(self):
        class Client:
            loads = iter(
                [
                    {"database_found": 1, "active_requests": 2},
                    {"database_found": 1, "active_requests": 0},
                    {"database_found": 1, "active_requests": 0},
                    {"database_found": 1, "active_requests": 0},
                ]
            )

            def execute_sql(self, *_args):
                return [next(self.loads)]

        gate = benchmark.DatabaseLoadGate(Client(), 3, "datamart_106", 0)
        gate.wait()
        self.assertTrue(gate.confirmed)
        gate.wait()

    def test_client_refreshes_expired_access_token_once(self):
        class Response:
            status = 200

            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.body

        class Opener:
            calls = 0

            def open(self, request, timeout):
                if request.full_url.endswith("/api/v1/security/refresh"):
                    return Response(b'{"access_token":"fresh"}')
                self.calls += 1
                if self.calls == 1:
                    raise HTTPError(
                        request.full_url,
                        401,
                        "expired",
                        {},
                        io.BytesIO(b'{"msg":"Token has expired"}'),
                    )
                self.last_authorization = request.headers["Authorization"]
                return Response(b'{"result":"ok"}')

        client = benchmark.SupersetClient.__new__(benchmark.SupersetClient)
        client.base_url = "http://superset:8088"
        client.opener = Opener()
        client.token = "expired"
        client.refresh_token = "refresh"
        status, body = client.request("GET", "/api/v1/example", auth=True)
        self.assertEqual((status, body), (200, {"result": "ok"}))
        self.assertEqual(client.opener.last_authorization, "Bearer fresh")

    def test_run_turn_sends_dashboard_page_context(self):
        client = benchmark.SupersetClient.__new__(benchmark.SupersetClient)
        sent = {}

        def request(method, path, **kwargs):
            if method == "POST":
                sent.update(kwargs["payload"])
                return 202, {"result": {"run_id": "run-1"}}
            return 200, {
                "result": {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "done",
                            "extra": {"run_id": "run-1"},
                        }
                    ]
                }
            }

        client.request = request
        client._stream = lambda *_args: ([], [])
        page_context = {
            "pageType": "dashboard",
            "dashboardContext": {"id": 44},
        }
        client.run_turn("thread-1", "analyst", "question", page_context)
        self.assertEqual(sent["page_context"], page_context)

    def test_grounded_fact_accepts_business_number_format(self):
        answer = "| Оператор | 23 854 |\n| Итого | 44,454 |"
        self.assertTrue(benchmark._answer_has_fact(answer, ["оператор"], 23854))
        self.assertTrue(benchmark._answer_has_fact(answer, ["итого"], 44454))

    def test_grounded_fact_accepts_decimal_trailing_zeros_and_unicode_minus(self):
        answer = "Доля: 24,00 %. Разница: −454."
        self.assertTrue(benchmark._answer_has_fact(answer, ["доля"], "24.0"))
        self.assertTrue(benchmark._answer_has_fact(answer, ["разница"], "-454"))
        self.assertFalse(benchmark._answer_has_fact(answer, ["разница"], "454"))
        self.assertTrue(
            benchmark._answer_has_fact("Доля: 88,3%", ["доля"], "88.26")
        )
        self.assertFalse(
            benchmark._answer_has_fact("Доля: 88,2%", ["доля"], "88.26")
        )

    def test_single_number_can_be_grounded_outside_sql_code(self):
        answer = "**2 037**\n```sql\nSELECT 999 AS result\n```"
        self.assertTrue(benchmark._answer_has_number(answer, 2037))
        self.assertFalse(benchmark._answer_has_number(answer, 999))

    def test_grounded_fact_accepts_text_and_equivalent_date_formats(self):
        self.assertTrue(
            benchmark._answer_has_fact(
                "Самый частый путь — Менеджер.", ["путь"], "Менеджер"
            )
        )
        self.assertTrue(
            benchmark._value_matches(
                "03.08.2026", "2026-08-03", Decimal("0")
            )
        )
        self.assertTrue(
            benchmark._answer_has_fact(
                "Дата: 3 августа 2026 года", ["дат"], "2026-08-03"
            )
        )

    def test_exact_golden_date_does_not_require_a_nearby_label(self):
        self.assertTrue(
            benchmark._answer_has_fact(
                "Больше всего звонков было 13 июля 2026 года.",
                ["дат", "день"],
                "2026-07-13",
            )
        )

    def test_last_successful_tool_ignores_recovered_failure(self):
        calls = [
            {"name": "execute_sql", "ok": False},
            {"name": "execute_sql", "ok": True, "output": '{"rows":[{"n":2}]}'},
        ]
        call = benchmark._last_successful_call(calls, "execute_sql")
        self.assertEqual(benchmark._tool_payload(call)["rows"], [{"n": 2}])

    def test_tool_payload_uses_structured_display_when_audit_text_is_cut(self):
        call = {
            "output": '{"rows":[{"n":2}],"executed_sql":"SELECT',
            "display": {"kind": "sql_result", "rows": [{"n": 2}]},
        }
        self.assertEqual(benchmark._tool_payload(call)["rows"], [{"n": 2}])

    def test_numeric_tolerance_is_explicit(self):
        self.assertTrue(
            benchmark._close_enough(
                Decimal("135.629"), Decimal("135.63"), Decimal("0.01")
            )
        )

    def test_correct_answer_passes_when_only_sql_shape_differs(self):
        case = {
            "id": "answer_first",
            "category": "business_answer",
            "profile": "analyst",
            "prompt": "question",
            "database_id": 1,
            "golden_sql": "golden",
            "_golden_rows": [{"share": "67.37"}],
            "sql_must_match": ["required_query_shape"],
            "facts": [{"golden_key": "share", "labels": ["доля"]}],
            "required_tools": ["execute_sql"],
        }
        turn = {
            "run_id": "run-1",
            "thread_uuid": "thread-1",
            "events": [],
            "stream_errors": [],
            "assistant": {
                "status": "complete",
                "content": "Доля: 67,4%.",
                "extra": {
                    "outcome": "success",
                    "turns": 1,
                    "tool_calls": [
                        {
                            "name": "execute_sql",
                            "ok": True,
                            "arguments": {"sql": "SELECT 67.37 AS share"},
                            "output": '{"rows":[{"share":67.37}]}',
                        }
                    ],
                },
            },
        }

        result = benchmark.validate_case(None, case, turn, 1.0)

        self.assertTrue(result["dimensions"]["sql"])
        self.assertFalse(
            result["sql_evidence"]["sql_pattern_checks"]["required_query_shape"]
        )
        self.assertTrue(result["dimensions"]["answer"])
        self.assertNotIn("sql", result["hard_gates"])
        self.assertTrue(result["passed"])

    def test_answer_uses_values_without_requiring_labels_or_unasked_facts(self):
        case = {
            "id": "answer_values",
            "database_id": 1,
            "golden_sql": "golden",
            "_golden_rows": [
                {
                    "status": 2060,
                    "application": 2971,
                    "difference": 911,
                    "total": 12689,
                }
            ],
            "facts": [
                {"golden_key": "status", "labels": ["статус"]},
                {"golden_key": "application", "labels": ["инструкц"]},
                {
                    "golden_key": "difference",
                    "labels": ["разниц"],
                    "tool_required": False,
                },
                {
                    "golden_key": "total",
                    "labels": ["итого"],
                    "tool_required": False,
                    "answer_required": False,
                },
            ],
        }
        calls = [
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT 2060, 2971"},
                "output": '{"rows":[{"status":2060,"application":2971}]}',
            }
        ]

        sql_ok, answer_ok, evidence = benchmark._validate_sql_case(
            None,
            case,
            calls,
            "Инструкцию выбрали 2 971 раз, статус — 2 060: на 911 больше.",
        )

        self.assertTrue(sql_ok and answer_ok)
        facts = {fact["golden_key"]: fact for fact in evidence["facts"]}
        self.assertTrue(facts["difference"]["found_in_answer"])
        self.assertFalse(facts["difference"]["found_near_label"])
        self.assertFalse(facts["total"]["answer_required"])
        self.assertFalse(facts["total"]["found_in_answer"])

    def test_forbidden_extra_claim_is_a_separate_hard_gate(self):
        case = {
            "id": "contradiction",
            "category": "business_answer",
            "profile": "analyst",
            "prompt": "compare",
            "answer_any": [r"20"],
            "answer_must_not_match": [r"два\s+непересекающихся\s+кластера"],
        }
        turn = {
            "run_id": "run-1",
            "thread_uuid": "thread-1",
            "events": [],
            "stream_errors": [],
            "assistant": {
                "status": "complete",
                "content": "Менеджер: 20. Это два непересекающихся кластера.",
                "extra": {"outcome": "success", "turns": 1, "tool_calls": []},
            },
        }

        result = benchmark.validate_case(None, case, turn, 1.0)

        self.assertTrue(result["dimensions"]["answer"])
        self.assertFalse(result["dimensions"]["answer_consistency"])
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["answer_evidence"]["forbidden_pattern_hits"],
            case["answer_must_not_match"],
        )

    def test_verification_query_does_not_hide_fact_rich_query(self):
        class Client:
            @staticmethod
            def execute_sql(_database_id, _sql):
                return [{"operator": 10, "total": 12}]

        case = {
            "id": "multi_sql",
            "database_id": 1,
            "golden_sql": "golden",
            "sql_must_match": ["source"],
            "facts": [
                {"golden_key": "operator", "labels": ["оператор"]},
                {
                    "golden_key": "total",
                    "labels": ["итого"],
                    "tool_required": False,
                },
            ],
        }
        calls = [
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT source, 10 AS n"},
                "output": '{"rows":[{"n":10}]}',
            },
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT 12 AS total"},
                "output": '{"rows":[{"total":12}]}',
            },
        ]
        sql_ok, answer_ok, evidence = benchmark._validate_sql_case(
            Client(), case, calls, "Оператор: 10. Итого: 12."
        )
        self.assertTrue(sql_ok)
        self.assertTrue(answer_ok)
        self.assertEqual(evidence["selected_tool_call_index"], 0)

    def test_multi_query_evidence_requires_explicit_opt_in(self):
        class Client:
            @staticmethod
            def execute_sql(_database_id, _sql):
                return [{"first": 10, "second": 20}]

        case = {
            "id": "multi_source",
            "database_id": 1,
            "golden_sql": "golden",
            "sql_must_match": ["table_a", "table_b"],
            "facts": [
                {"golden_key": "first", "labels": ["перв"]},
                {"golden_key": "second", "labels": ["втор"]},
            ],
        }
        calls = [
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT 10 FROM table_a"},
                "output": '{"rows":[{"first":10}]}',
            },
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT 20 FROM table_b"},
                "output": '{"rows":[{"second":20}]}',
            },
        ]
        sql_ok, _, _ = benchmark._validate_sql_case(
            Client(), case, calls, "Первый: 10. Второй: 20."
        )
        self.assertFalse(sql_ok)
        case["allow_multiple_sql_calls"] = True
        sql_ok, answer_ok, evidence = benchmark._validate_sql_case(
            Client(), case, calls, "Первый: 10. Второй: 20."
        )
        self.assertTrue(sql_ok and answer_ok)
        self.assertEqual(evidence["sql_evidence_mode"], "all_successful_calls")

    def test_sql_server_unicode_literal_matches_plain_literal_contract(self):
        class Client:
            @staticmethod
            def execute_sql(_database_id, _sql):
                return [{"total": 112}]

        case = {
            "id": "unicode_literal",
            "database_id": 1,
            "golden_sql": "golden",
            "sql_must_match": [r"source\s*=\s*'custom'"],
            "facts": [{"golden_key": "total", "labels": ["итого"]}],
        }
        calls = [
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT 112 FROM t WHERE source = N'custom'"},
                "output": '{"rows":[{"total":112}]}',
            }
        ]
        sql_ok, answer_ok, _ = benchmark._validate_sql_case(
            Client(), case, calls, "Итого: 112."
        )
        self.assertTrue(sql_ok and answer_ok)

    def test_frozen_golden_avoids_a_second_database_query(self):
        class Client:
            @staticmethod
            def execute_sql(*_args):
                raise AssertionError("live golden SQL must not run")

        case = {
            "id": "frozen",
            "database_id": 1,
            "golden_sql": "SELECT 7 AS total",
            "_golden_rows": [{"total": 7}],
            "facts": [{"golden_key": "total", "labels": ["итого"]}],
        }
        calls = [
            {
                "name": "execute_sql",
                "ok": True,
                "arguments": {"sql": "SELECT 7 AS total"},
                "output": '{"rows":[{"total":7}]}',
            }
        ]
        sql_ok, answer_ok, evidence = benchmark._validate_sql_case(
            Client(), case, calls, "Итого: 7."
        )
        self.assertTrue(sql_ok and answer_ok)
        self.assertEqual(evidence["golden_source"], "frozen")

    def test_allowlisted_lifecycle_warning_keeps_valid_chart(self):
        class Client:
            @staticmethod
            def get_asset(_kind, _asset_id):
                return {
                    "datasource_id": 89,
                    "viz_type": "echarts_timeseries_line",
                    "slice_name": "[BENCH] chart",
                    "params": (
                        '{"x_axis":"created_hour","metrics":[{"column":'
                        '{"column_name":"total_calls"}}],"adhoc_filters":[]}'
                    ),
                }

            @staticmethod
            def asset_url(_kind, asset_id):
                return f"http://superset/chart/{asset_id}/"

        expectation = {
            "min_count": 1,
            "max_count": 1,
            "dataset_id": 89,
            "viz_types": ["echarts_timeseries_line"],
            "title_equals": "[BENCH] chart",
            "expected_params": {
                "x_axis": "created_hour",
                "metric_columns": ["total_calls"],
                "filters": [],
            },
            "allow_warnings": False,
            "allowed_warning_patterns": [
                "virtual dataset",
                "time_grain=.*was ignored because the x-axis column is not a temporal type",
            ],
        }
        calls = [
            {
                "name": "generate_chart",
                "ok": True,
                "arguments": {"save_chart": True},
                "output": (
                    '{"chart_id":229,"warnings":'
                    '["time_grain=\'PT1H\' was ignored because the x-axis column '
                    'is not a temporal type.",'
                    '"This chart uses a virtual dataset."]}'
                ),
            }
        ]
        passed, _, _ = benchmark._validate_chart(Client(), expectation, calls)
        self.assertTrue(passed)

    def test_release_manifest_is_dashboard_grounded(self):
        document = generator.build_document()
        ids = [case["id"] for case in document["cases"]]
        self.assertEqual(len(ids), 26)
        self.assertEqual(len(set(ids)), 26)
        self.assertEqual(
            Counter(case["category"] for case in document["cases"]),
            {"business_answer": 26},
        )
        self.assertTrue(
            all(
                case["page_context"]["dashboardContext"]["id"] == 44
                and case["required_tools"] == ["execute_sql"]
                for case in document["cases"]
            )
        )
        expected_chart_ids = {
            268,
            269,
            270,
            271,
            272,
            273,
            274,
            275,
            276,
            277,
            278,
            279,
            280,
        }
        self.assertEqual(
            {
                chart_id
                for case in document["cases"]
                for chart_id in case["source_chart_ids"]
            },
            expected_chart_ids,
        )
        self.assertTrue(
            all(
                269
                in {
                    chart["id"]
                    for chart in case["page_context"]["dashboardContext"]["charts"]
                }
                for case in document["cases"]
            )
        )
        table_pattern = next(
            case for case in document["cases"] if case["id"] == "month_automation_level"
        )["sql_must_match"][0]
        self.assertTrue(re.search(table_pattern, "FROM texas_unique_callers_summary"))
        self.assertTrue(re.search(table_pattern, "FROM dbo.texas_unique_callers_summary"))
        by_id = {case["id"]: case for case in document["cases"]}
        manager_facts = {
            fact["golden_key"]: fact
            for fact in by_id["month_manager_vs_automated"]["facts"]
        }
        self.assertFalse(manager_facts["total_calls"]["answer_required"])
        self.assertIn(
            "answer_must_not_match", by_id["month_manager_vs_automated"]
        )
        language_facts = {
            fact["golden_key"]: fact
            for fact in by_id["ivr_language_split"]["facts"]
        }
        self.assertFalse(language_facts["total_choices"]["answer_required"])

    def test_dashboard_auto_layout_contract(self):
        layout = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "children": ["GRID_ID"]},
            "GRID_ID": {
                "type": "GRID",
                "parents": ["ROOT_ID"],
                "children": ["ROW-1"],
            },
            "ROW-1": {
                "type": "ROW",
                "parents": ["ROOT_ID", "GRID_ID"],
                "children": ["COLUMN-1", "COLUMN-2"],
            },
        }
        for index, chart_id in enumerate((222, 223), 1):
            layout[f"COLUMN-{index}"] = {
                "type": "COLUMN",
                "parents": ["ROOT_ID", "GRID_ID", "ROW-1"],
                "children": [f"CHART-{chart_id}"],
                "meta": {"width": 6},
            }
            layout[f"CHART-{chart_id}"] = {
                "id": f"CHART-{chart_id}",
                "type": "CHART",
                "parents": ["ROOT_ID", "GRID_ID", "ROW-1", f"COLUMN-{index}"],
                "meta": {"chartId": chart_id, "height": 50, "width": 6},
            }
        checks, rows, chart_ids = benchmark._dashboard_layout_evidence(
            layout, [222, 223], True
        )
        self.assertTrue(all(checks.values()))
        self.assertEqual(rows, [[222, 223]])
        self.assertEqual(chart_ids, [222, 223])

    def test_aggregate_requires_and_counts_three_complete_repeats(self):
        reports = []
        for repeat in range(3):
            reports.append(
                {
                    "run_name": f"r{repeat + 1}",
                    "benchmark_version": "2",
                    "dataset_name": "dataset",
                    "cases_sha256": "abc",
                    "case_ids": ["case-1"],
                    "results": [
                        {
                            "case_id": "case-1",
                            "category": "safety",
                            "passed": True,
                            "latency_seconds": 1,
                            "dimensions": {
                                "completion": True,
                                "sql": None,
                                "answer": True,
                                "tool_contract": True,
                                "asset": None,
                                "safety": True,
                            },
                            "sql_evidence": {"golden_source": None},
                            "langfuse": {"trace_id": f"trace-{repeat}"},
                        }
                    ],
                }
            )
        result = aggregate.aggregate_reports(reports)
        self.assertEqual(result["total_case_runs"], 3)
        self.assertEqual(result["summary"]["stable_cases_all_repeats"], 1)
        self.assertEqual(result["summary"]["langfuse_synced"], 3)
        self.assertEqual(result["summary"]["golden_sources"], {"missing": 3})


if __name__ == "__main__":
    unittest.main()
