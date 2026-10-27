"""Explicit service target while the birth-date collection policy is deferred."""

SERVICE_SCHEMA_REVISION = "f4b8c1d9a620"
DEFERRED_BIRTH_REVISION = "d2f6a9c4e731"


def include_schema_object(obj, name, type_, reflected, compare_to):
    """Keep autogenerate from removing a legacy column absent from the ORM.

    Existing databases may retain birth_date or have already applied the old
    removal revision. Neither state is changed by onboarding V2.
    """
    return not (
        type_ == "column"
        and name == "birth_date"
        and obj.table.name == "user_account"
    )


def service_schema_ready(revisions: set[str], expected: str = SERVICE_SCHEMA_REVISION) -> bool:
    return expected in revisions and revisions <= {expected, DEFERRED_BIRTH_REVISION}
