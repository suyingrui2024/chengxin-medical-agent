import unittest

from medagent.plugins import PluginHost, RuntimeContext


class DemoPlugin:
    name = "demo"

    def mount(self, context: RuntimeContext):
        return context.services.provide("demo_service", {"ready": True})


class RuntimeTestCase(unittest.TestCase):
    def test_plugin_effect_is_reversible(self):
        host = PluginHost([DemoPlugin()])
        context = host.start()
        self.assertEqual(context.services.require("demo_service"), {"ready": True})
        host.stop()
        self.assertNotIn("demo_service", context.services.names())


if __name__ == "__main__":
    unittest.main()

