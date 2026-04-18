"""E2E tests: glossary term CRUD and search integration."""

import uuid

import pytest


@pytest.mark.e2e
def test_create_and_search_glossary_term(api):
    """Create glossary term -> verify it appears in glossary listing."""
    term_id = f"test-e2e-glossary-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Create glossary term
        create_resp = api.post("/api/v1/admin/glossary", json={
            "id": term_id,
            "term": "Example Platform",
            "term_ko": "오레오 플랫폼",
            "definition": "GS Retail AI Operations Platform for knowledge management",
            "synonyms": ["example", "예시"],
            "kb_id": "test-e2e-glossary",
            "status": "approved",
            "scope": "global",
            "source": "manual",
        })
        assert create_resp.status_code == 200, f"Create term failed: {create_resp.text}"
        assert create_resp.json()["success"] is True

        # 2. Verify term can be fetched directly
        get_resp = api.get(f"/api/v1/admin/glossary/{term_id}")
        assert get_resp.status_code == 200, f"Term {term_id} not retrievable after creation"
        fetched = get_resp.json()
        assert fetched.get("term") == "Example Platform"

    finally:
        # Cleanup
        api.delete(f"/api/v1/admin/glossary/{term_id}")


@pytest.mark.e2e
def test_glossary_crud(api):
    """Full CRUD: create -> read -> update -> delete."""
    term_id = f"test-e2e-crud-{uuid.uuid4().hex[:8]}"

    # 1. CREATE
    create_resp = api.post("/api/v1/admin/glossary", json={
        "id": term_id,
        "term": "Test CRUD Term",
        "term_ko": "테스트 CRUD 용어",
        "definition": "A term created for CRUD testing",
        "kb_id": "test-e2e-glossary",
        "status": "pending",
        "scope": "kb",
        "source": "manual",
    })
    assert create_resp.status_code == 200
    assert create_resp.json()["success"] is True

    # 2. READ
    get_resp = api.get(f"/api/v1/admin/glossary/{term_id}")
    assert get_resp.status_code == 200
    term = get_resp.json()
    assert term["term"] == "Test CRUD Term"
    assert term["definition"] == "A term created for CRUD testing"

    # 3. UPDATE
    patch_resp = api.patch(f"/api/v1/admin/glossary/{term_id}", json={
        "definition": "Updated definition for CRUD testing",
        "status": "approved",
    })
    assert patch_resp.status_code == 200

    # Verify update
    get_resp2 = api.get(f"/api/v1/admin/glossary/{term_id}")
    assert get_resp2.status_code == 200
    updated_term = get_resp2.json()
    assert updated_term["definition"] == "Updated definition for CRUD testing"

    # 4. DELETE
    delete_resp = api.delete(f"/api/v1/admin/glossary/{term_id}")
    assert delete_resp.status_code == 200

    # Verify deleted
    get_resp3 = api.get(f"/api/v1/admin/glossary/{term_id}")
    assert get_resp3.status_code == 404
