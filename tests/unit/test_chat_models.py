from src.stores.postgres.models import ChatConversationModel, ChatMessageModel


def test_conversation_table_name():
    assert ChatConversationModel.__tablename__ == "chat_conversations"
    cols = {c.name for c in ChatConversationModel.__table__.columns}
    assert {"id", "user_id", "org_id", "title", "kb_ids",
            "created_at", "updated_at", "deleted_at"} <= cols


def test_message_table_name():
    assert ChatMessageModel.__tablename__ == "chat_messages"
    cols = {c.name for c in ChatMessageModel.__table__.columns}
    assert {"id", "conversation_id", "role", "content_enc",
            "chunks", "meta", "trace_id", "created_at"} <= cols


def test_message_role_check():
    """role column must have a check constraint user|assistant."""
    constraints = [c for c in ChatMessageModel.__table__.constraints
                   if "role" in str(c).lower()]
    assert constraints, "role check constraint missing"
