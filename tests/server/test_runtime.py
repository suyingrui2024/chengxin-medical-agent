import unittest

from unittest.mock import patch

from medagent.server.runtime import build_runtime


class RuntimeTestCase(unittest.TestCase):
    def test_runtime_services_are_released_on_stop(self):
        host = build_runtime(backend="demo")
        context = host.start()
        engine = context.services.require("consultation")
        self.assertIs(engine.events, context.services.require("events"))
        self.assertIs(engine.content, context.services.require("content"))
        with self.assertRaises(ValueError):
            host.start()
        host.stop()
        self.assertEqual(context.services.names(), [])
        with self.assertRaises(RuntimeError):
            context.services.require("consultation")
        self.assertIsNot(host.start().services.require("consultation"), engine)
        host.stop()

    def test_failed_start_does_not_publish_partial_services(self):
        host = build_runtime(backend="demo")
        with patch("medagent.server.runtime.ConsultationEngine", side_effect=ValueError("failed")):
            with self.assertRaises(ValueError):
                host.start()
        self.assertEqual(host.context.services.names(), [])


if __name__ == "__main__":
    unittest.main()
