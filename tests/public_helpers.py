"""Translate internal fixtures when deliberately exercising the public API."""

from server.public import references


def rebuild_payload(reader, data):
    data = data.copy()

    def ref(value):
        if "flight_id" in value:
            return value
        record = reader.flight(value["provider"], value["provider_id"])
        return {"flight_id": references(reader, [record])[(record.provider, record.provider_id)]}

    data["prefix"] = [ref(value) for value in data["prefix"]]
    if data.get("chosen_next"):
        data["chosen_next"] = ref(data["chosen_next"])
    return data
