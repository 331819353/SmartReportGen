"""用内存 DuckDB 验证过滤、SQL 作用域与场景权限；无需业务数据。"""
import unittest
from unittest.mock import patch

from smartreportgen.connectors.api import APIConnector
from smartreportgen.connectors.base import ConnectorError
from smartreportgen.auth import Principal
from smartreportgen.permission import PermissionDenied, access_for
from smartreportgen.scenario_filters import query_in_scenario, table_filters
from smartreportgen.services import describe_table, source_scene_values


class ScenarioFiltersTest(unittest.TestCase):
    def setUp(self):
        self.source = {"type": "api", "name": "测试数据源"}
        self.connector = APIConnector(self.source, "")
        records = {
            "Sales": [
                {"id": 1, "region": "华东", "Status": 1, "amount": 10},
                {"id": 2, "region": "华南", "Status": 1, "amount": 20},
                {"id": 3, "region": "华北", "Status": 1, "amount": 999},
                {"id": 4, "region": "华东", "Status": 0, "amount": 999},
                {"id": 5, "region": None, "Status": 1, "amount": 5},
                {"id": 6, "region": "O'Reilly -- x%", "Status": 1, "amount": 4},
            ],
            "Lookup": [{"id": 1, "valid": "Y"}, {"id": 2, "valid": "N"}, {"id": 3, "valid": "Y"}],
        }
        self.connector.fetch = records.__getitem__
        self.dataset = {"tables": [
            {"table_name": "Sales", "row_filters": [
                {"column": "region", "values": ["华东", "华南", "O'Reilly -- x%", None]},
                {"column": "Status", "values": ["1"]},
            ]},
            {"table_name": "Lookup", "row_filters": [{"column": "valid", "values": ["Y"]}]},
        ]}

    def run_sql(self, sql, allowed=None):
        return query_in_scenario(self.source, self.dataset, sql, 30, self.connector, allowed)["rows"]

    def test_filter_before_aggregation_and_preserve_values(self):
        self.assertEqual(self.run_sql("SELECT COUNT(*), SUM(amount) FROM Sales"), [[4, 39]])

    def test_join_filters_both_tables_without_removing_unmatched_rows(self):
        self.assertEqual(
            self.run_sql("SELECT s.id, l.valid FROM Sales s LEFT JOIN Lookup l ON l.id = s.id ORDER BY s.id"),
            [[1, "Y"], [2, None], [5, None], [6, None]],
        )

    def test_cte_shadowing_and_repeated_references(self):
        self.assertEqual(self.run_sql("WITH sales AS (SELECT * FROM Sales) SELECT COUNT(*) FROM sales"), [[4]])
        self.assertEqual(self.run_sql(
            "SELECT s.id FROM Sales s WHERE EXISTS "
            "(WITH sales AS (SELECT * FROM Lookup) SELECT * FROM sales WHERE sales.id = s.id)"
        ), [[1]])
        self.assertEqual(self.run_sql(
            "SELECT COUNT(*) FROM (SELECT id FROM Sales UNION ALL SELECT id FROM Sales) q"
        ), [[8]])

    def test_refuse_out_of_scope_and_ungranted_tables(self):
        for sql, allowed in [("SELECT * FROM Lookup", {"sales"}), ("SELECT * FROM missing", None),
                             ("SELECT * FROM read_json_auto('/tmp/unapproved.json')", None)]:
            with self.subTest(sql=sql), self.assertRaises(PermissionDenied):
                self.run_sql(sql, allowed)

    def test_profile_uses_filtered_count_samples_and_extrema(self):
        with patch("smartreportgen.services.get_connector", return_value=self.connector):
            result = describe_table(self.source, "Sales", sample_rows=2, profile_rows=2, dataset=self.dataset)
        self.assertEqual(result["row_count"], 4)
        amount = next(c for c in result["profile"] if c["column"] == "amount")
        self.assertEqual((amount["full_min"], amount["full_max"]), (4, 20))

    def test_validate_column_and_values(self):
        columns = self.connector.columns("Sales")
        self.assertEqual(table_filters([{"column": "status", "values": [1]}], "Sales", columns),
                         [{"column": "Status", "values": [1]}])
        for rule in [{"column": "missing", "values": [1]}, {"column": "Status", "values": []},
                     {"column": "Status", "values": [{"raw_sql": "1=1"}]}]:
            with self.subTest(rule=rule), self.assertRaises(ConnectorError):
                table_filters([rule], "Sales", columns)

    def test_personal_scene_filters_intersect_the_fixed_source_range(self):
        self.dataset["tables"][0]["source_row_filters"] = [{"column": "region", "values": ["华东"]}]
        result = self.run_sql("SELECT id FROM Sales")
        self.assertEqual(result, [[1]])

    def test_personal_scene_filter_picker_reads_only_authorized_rows(self):
        policy = {"table_name": "Sales", "source_row_filters": [{"column": "region", "values": ["华东", "华南"]}]}
        with patch("smartreportgen.services.get_connector", return_value=self.connector):
            first = source_scene_values(self.source, policy, "region", "", 1, 0)
            self.assertEqual(first, {"values": ["华东"], "has_more": True})
            second = source_scene_values(self.source, policy, "region", "", 1, 1)
            self.assertEqual(second, {"values": ["华南"], "has_more": False})
            self.assertEqual(source_scene_values(self.source, policy, "region", "华北", 10, 0)["values"], [])

    def test_personal_scene_access_respects_current_source_policy(self):
        principal = Principal("owner", roles=["analyst"])
        config = {"scenario_tables": [{"table_name": "Sales", "roles": ["analyst"]},
                                      {"table_name": "Lookup", "employees": ["other"]}]}
        rows = [{"dataset_id": 11, "owner_emp_no": None, "datasource_id": 8, "table_name": "Lookup", "config": None},
                {"dataset_id": 12, "owner_emp_no": "owner", "datasource_id": 8, "table_name": "Sales", "config": config},
                {"dataset_id": 12, "owner_emp_no": "owner", "datasource_id": 8, "table_name": "Lookup", "config": config}]
        for row in rows:
            row.update(granted=True, access_level=None)
        with patch("smartreportgen.permission.db.fetchall", return_value=rows):
            self.assertEqual(access_for(principal).tables, {11: {"lookup"}, 12: {"sales"}})
            principal.roles.clear()
            self.assertEqual(access_for(principal).tables, {11: {"lookup"}})

    def test_value_options_use_source_values_and_keep_null(self):
        first = self.connector.distinct_values("Sales", "region", "", 2, 0)
        self.assertEqual(first, {"values": [None, "O'Reilly -- x%"], "has_more": True})
        self.assertEqual(self.connector.distinct_values("Sales", "region", "", 2, 2)["values"], ["华东", "华北"])
        self.assertEqual(self.connector.distinct_values("Sales", "region", "X%", 10, 0)["values"], ["O'Reilly -- x%"])
        self.assertEqual(self.connector.distinct_values("Sales", "Status", "", 10, 0)["values"], [0, 1])
        with self.assertRaises(ConnectorError):
            self.connector.distinct_values("Sales", "missing", "", 10, 0)


if __name__ == "__main__":
    unittest.main()
