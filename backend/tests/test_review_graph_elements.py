from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.routers.review import list_pending_reviews
from app.graph.repository import GraphRepository


def test_pending_identity_reviews_require_admin_role():
    with pytest.raises(HTTPException) as exc_info:
        list_pending_reviews(
            mode="pending",
            current_user=SimpleNamespace(id=1, role="user"),
            db=None,
        )

    assert exc_info.value.status_code == 403


def test_person_neighborhood_graph_elements_maps_two_hop_subgraph():
    repo = GraphRepository.__new__(GraphRepository)

    def fake_run_read_query(cypher, parameters):
        assert "[*1..2]" in cypher
        assert "NOT 'Passage_Info' IN labels(nodes(path)[idx])" in cypher
        assert "NOT 'Person_Nodes' IN labels(nodes(path)[idx])" in cypher
        assert parameters == {"person_ids": [1, 2]}
        return {
            "records": [
                {
                    "nodes": [
                        {
                            "element_id": "n1",
                            "labels": ["Person_Nodes"],
                            "properties": {"person_id": 1, "name": "张三"},
                        },
                        {
                            "element_id": "n2",
                            "labels": ["Passage_Info"],
                            "properties": {"doc_id": 10, "title": "墓志"},
                        },
                        {
                            "element_id": "n3",
                            "labels": ["Life_Events"],
                            "properties": {"event_id": "le_1_1", "event_type": "任职"},
                        },
                        {
                            "element_id": "n4",
                            "labels": ["Person_Nodes"],
                            "properties": {"person_id": 3, "name": "李四"},
                        },
                    ],
                    "edges": [
                        {
                            "element_id": "r1",
                            "start_element_id": "n1",
                            "end_element_id": "n2",
                            "type": "在文章中",
                            "properties": {"level": 3},
                        },
                        {
                            "element_id": "r2",
                            "start_element_id": "n1",
                            "end_element_id": "n3",
                            "type": "生平",
                            "properties": {},
                        },
                        {
                            "element_id": "r3",
                            "start_element_id": "n1",
                            "end_element_id": "n4",
                            "type": "person_relation",
                            "properties": {"codes": ["F"], "note": None},
                        },
                    ],
                }
            ]
        }

    repo.run_read_query = fake_run_read_query

    result = repo.get_person_neighborhood_graph_elements([2, 1, 1], max_hops=2)

    assert result["nodes"] == [
        {
            "id": "Person_Nodes_1",
            "label": "张三",
            "type": "Person_Nodes",
            "properties": {"person_id": 1, "name": "张三", "review_focus": True},
        },
        {
            "id": "Passage_Info_10",
            "label": "墓志",
            "type": "Passage_Info",
            "properties": {"doc_id": 10, "title": "墓志"},
        },
        {
            "id": "Life_Events_le_1_1",
            "label": "任职",
            "type": "Life_Events",
            "properties": {"event_id": "le_1_1", "event_type": "任职"},
        },
        {
            "id": "Person_Nodes_3",
            "label": "李四",
            "type": "Person_Nodes",
            "properties": {"person_id": 3, "name": "李四"},
        },
    ]
    assert result["edges"] == [
        {
            "source": "Person_Nodes_1",
            "target": "Passage_Info_10",
            "label": "在文章中",
            "properties": {},
        },
        {
            "source": "Person_Nodes_1",
            "target": "Life_Events_le_1_1",
            "label": "生平",
            "properties": {},
        },
        {
            "source": "Person_Nodes_1",
            "target": "Person_Nodes_3",
            "label": "F",
            "properties": {"codes": ["F"], "note": None},
        },
    ]
