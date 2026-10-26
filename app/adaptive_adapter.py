"""Только искусственный транспорт нового контекста; сетевого режима и ввода ключа нет."""

import copy
import json

import adaptive_contract as contract
import benchmark_adapter as transport
import input_search_adapter as old_adapter
import nebius_client as client

FUNCTION_NAME = "input_search_action"


def build_request(context):
    context = contract.validate_context(context)
    public = {k: v for k, v in context.items() if k not in ("instruction", "action_schema")}
    messages = [{"role": "system", "content": context["instruction"]},
                {"role": "user", "content": json.dumps(public, ensure_ascii=False, sort_keys=True)}]
    return client.build_function_request(messages, context["action_schema"], FUNCTION_NAME,
                                         max_completion_tokens=512, max_request_bytes=16000)


class SyntheticTransport:
    def __init__(self, responder=None):
        self.responder = responder
        self.calls = 0

    def __call__(self, body):
        self.calls += 1
        request = json.loads(body)
        context = json.loads(request["messages"][1]["content"])
        context.update(instruction=request["messages"][0]["content"],
                       action_schema=request["tools"][0]["function"]["parameters"])
        context = contract.validate_context(context)
        reply = (old_adapter.artificial_response(contract.minimax_boundary(context), request["model"])
                 if self.responder is None else self.responder(copy.deepcopy(context)))
        return reply if isinstance(reply, (str, bytes)) else json.dumps(reply, ensure_ascii=False).encode()


class OfflineSession(transport.DiagnosticSession):
    function_name = FUNCTION_NAME
    transport_type = SyntheticTransport

    def __init__(self, synthetic_transport=None):
        super().__init__(synthetic_transport=synthetic_transport or SyntheticTransport(), allow_network=False, max_requests=4)

    def _validate_context(self, context):
        return contract.validate_context(context)

    def _build_request(self, context):
        return build_request(context)

    def _validate_action(self, arguments, context):
        return contract.validate_action(arguments, context)

    def _send(self, body, api_key):
        raise client.NetworkDisabled("Новый сравнительный режим допускает только искусственный транспорт.")

    def _continuity(self, context):
        identity = contract.shared.digest({k: context[k] for k in ("contract", "initial", "candidates")})
        if self._sample_digest is None:
            if context["history"]:
                raise client.ClientError("Сеанс должен начинаться с пустой историей.")
            self._sample_digest = identity
            return
        previous = self._last_context["history"]
        if (identity != self._sample_digest or self._last_action["action"] != "try_input"
                or len(context["history"]) != len(previous) + 1 or context["history"][:-1] != previous
                or context["history"][-1]["candidate_id"] != self._last_action["candidate_id"]):
            raise client.ClientError("История не продолжает последнюю разрешённую пробу.")
