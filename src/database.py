import json
from decimal import Decimal
from chainlit.data.dynamodb import DynamoDBDataLayer

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

class DecimalDynamoDBWrapper:
    def __init__(self, data_layer: DynamoDBDataLayer):
        self.data_layer = data_layer
        self._wrap_serialize_item()
        self._wrap_deserialize_item()

    def _wrap_serialize_item(self):
        original_serialize = self.data_layer._serialize_item

        def convert_floats_to_decimal(obj):
            if isinstance(obj, dict):
                return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
            elif isinstance(obj, list):
                return [convert_floats_to_decimal(item) for item in obj]
            elif isinstance(obj, float):
                return Decimal(str(obj))
            return obj

        def wrapped_serialize_item(item):
            converted_item = convert_floats_to_decimal(item)
            return original_serialize(converted_item)

        self.data_layer._serialize_item = wrapped_serialize_item

    def _wrap_deserialize_item(self):
        original_deserialize = self.data_layer._deserialize_item

        def convert_decimal_to_float(obj):
            if isinstance(obj, dict):
                return {key: convert_decimal_to_float(value) for key, value in obj.items()}
            elif isinstance(obj, list):
                return [convert_decimal_to_float(item) for item in obj]
            elif isinstance(obj, Decimal):
                return float(obj)
            return obj

        def wrapped_deserialize_item(item):
            deserialized_item = original_deserialize(item)
            return convert_decimal_to_float(deserialized_item)

        self.data_layer._deserialize_item = wrapped_deserialize_item