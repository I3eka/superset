# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""API response contracts for AI commands."""

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask.testing import FlaskClient
from pytest_mock import MockerFixture

from superset.commands.ai.exceptions import (
    AIChatFeedbackInvalidError,
    AIChatMessageNotFoundError,
)


@pytest.mark.parametrize(
    "app", [{"FEATURE_FLAGS": {"AI_ASSISTANT": True}}], indirect=True
)
@pytest.mark.usefixtures("full_api_access")
@pytest.mark.parametrize(
    "command_error,status_code",
    [
        (AIChatFeedbackInvalidError("Only assistant messages can be rated."), 422),
        (AIChatMessageNotFoundError(), 404),
        (None, 200),
    ],
    ids=["invalid-target", "missing-or-foreign-target", "accepted"],
)
def test_feedback_command_response(
    client: FlaskClient,
    mocker: MockerFixture,
    command_error: Exception | None,
    status_code: int,
) -> None:
    """Validation errors remain 422; hidden targets remain 404, not server errors."""
    mocker.patch("superset.ai.api.AIRestApi._reject_if_unconfigured", return_value=None)
    mocker.patch("superset.ai.api.AIRestApi._user_id", return_value=1)
    command = mocker.patch("superset.commands.ai.SubmitAIChatFeedbackCommand")
    command.return_value.run.side_effect = command_error

    response = client.post(
        "/api/v1/ai/feedback", json={"message_uuid": "test-message", "liked": True}
    )

    assert response.status_code == status_code
    command.assert_called_once_with("test-message", 1, liked=True, comment=None)


@pytest.mark.parametrize(
    "app", [{"FEATURE_FLAGS": {"AI_ASSISTANT": True}}], indirect=True
)
@pytest.mark.usefixtures("full_api_access")
@pytest.mark.parametrize(
    "server_timezone,stored_time",
    [
        ("UTC", datetime(2026, 4, 16, 10, 20, 30, 123456)),
        ("Asia/Aqtau", datetime(2026, 4, 16, 15, 20, 30, 123456)),
        ("America/New_York", datetime(2026, 4, 16, 6, 20, 30, 123456)),
        (
            "America/New_York",
            datetime(
                2026, 4, 16, 15, 50, 30, 123456, tzinfo=timezone(timedelta(hours=5.5))
            ),
        ),
        ("UTC", None),
    ],
    ids=["utc-server", "east-server", "west-server", "aware-timestamp", "missing"],
)
def test_conversation_timestamps_include_timezone(
    client: FlaskClient,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    server_timezone: str,
    stored_time: datetime | None,
) -> None:
    """Server-local audit dates must identify the same instant in every browser."""
    from superset.models.ai import AIChatMessage, AIChatThread

    thread = AIChatThread(
        uuid=uuid4(),
        status="active",
        created_on=stored_time,
        changed_on=stored_time,
    )
    message = AIChatMessage(
        id=1,
        uuid=uuid4(),
        role="user",
        content="Hello",
        status="complete",
        created_on=stored_time,
    )
    thread.messages = [message]
    mocker.patch("superset.ai.api.AIRestApi._reject_if_unconfigured", return_value=None)
    mocker.patch("superset.ai.api.AIRestApi._user_id", return_value=1)
    mocker.patch(
        "superset.daos.ai.AIChatThreadDAO.find_all_for_user", return_value=[thread]
    )
    mocker.patch(
        "superset.daos.ai.AIChatThreadDAO.find_by_uuid_for_user", return_value=thread
    )
    mocker.patch(
        "superset.daos.ai.AIChatMessageDAO.find_for_thread", return_value=[message]
    )
    mocker.patch("superset.daos.ai.AIChatMessageDAO.counts_for_threads", return_value={})
    mocker.patch(
        "superset.daos.ai.AIChatFeedbackDAO.find_verdicts_for_user", return_value={}
    )

    try:
        with monkeypatch.context() as context:
            context.setenv("TZ", server_timezone)
            time.tzset()
            listing = client.get("/api/v1/ai/thread/")
            detail = client.get(f"/api/v1/ai/thread/{thread.uuid}")
    finally:
        time.tzset()

    assert listing.status_code == detail.status_code == 200
    expected = "2026-04-16T10:20:30.123456+00:00" if stored_time else None
    for result in (listing.json["result"][0], detail.json["result"]):
        assert result["created_on"] == expected
        assert result["changed_on"] == expected
    assert detail.json["result"]["messages"][0]["created_on"] == expected
