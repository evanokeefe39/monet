import logging

from monet.config import ChatConfig
from monet.orchestration.prebuilt.chat._lc import _load_model

_log = logging.getLogger("monet.server.smoke")


async def smoke_test_models(config: ChatConfig) -> None:
    """Verify that configured models are available and responsive.

    This helps 'fail fast' on server boot if a model has been decommissioned
    or the API key is invalid, rather than waiting for the first user prompt.
    """
    models_to_test = {"triage": config.triage_model, "respond": config.respond_model}

    for label, model_str in models_to_test.items():
        _log.info("Smoke testing %s model: %s", label, model_str)
        try:
            model = _load_model(model_str)
            # A trivial call to ensure the model is reachable and not decommed.
            # We use a very low max_tokens to keep it cheap.
            await model.ainvoke("ping", max_tokens=5)
        except Exception as exc:
            _log.error("Smoke test failed for %s model (%s): %s", label, model_str, exc)
            raise RuntimeError(
                f"Model smoke test failed for {label} ({model_str}). "
                "The model might be decommissioned or the API key is invalid. "
                f"Error: {exc}"
            ) from exc

    _log.info("Model smoke tests passed.")
