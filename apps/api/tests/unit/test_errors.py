from plimsoll_contracts.errors import ErrorCode, ErrorEnvelope


def test_catalogue_matches_the_api_contract() -> None:
    assert {c.value for c in ErrorCode} == {
        "VALIDATION_FAILED",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "CONFLICT",
        "IDEMPOTENCY_KEY_REUSED",
        "TEST_NOT_RUNNABLE",
        "TARGET_NOT_ALLOWED",
        "INSUFFICIENT_CAPACITY",
        "REPO_UNREACHABLE",
        "RATE_LIMITED",
        "INTERNAL",
    }


def test_envelope_serialises_to_the_documented_shape() -> None:
    envelope = ErrorEnvelope.of(
        ErrorCode.NOT_FOUND, "No such project.", request_id="req-1", details={"id": "x"}
    )
    assert envelope.model_dump(mode="json", exclude_none=True) == {
        "error": {
            "code": "NOT_FOUND",
            "message": "No such project.",
            "details": {"id": "x"},
            "requestId": "req-1",
        }
    }


def test_details_omitted_when_absent() -> None:
    envelope = ErrorEnvelope.of(ErrorCode.INTERNAL, "Boom.", request_id="req-2")
    dumped = envelope.model_dump(mode="json", exclude_none=True)
    assert "details" not in dumped["error"]
