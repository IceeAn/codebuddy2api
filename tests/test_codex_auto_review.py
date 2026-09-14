"""自动审批模型映射的配置优先级、用户隔离与请求策略。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import unittest
from unittest import mock

import config
from src.admin_router import AdminSettingsUpdate, get_admin_settings, save_admin_settings
from src.auth_types import AuthenticatedUser
from src.request_processor import RequestProcessor
from src.user_settings_schema import coerce_user_setting
from tests.helpers import ConfigIsolationMixin

KEY = 'CODEBUDDY_CODEX_AUTO_REVIEW_MODEL'


class CodexAutoReviewTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def prepare(self, model='codex-auto-review', user=None):
        return RequestProcessor.prepare_request({'model': model, 'messages': [{'role': 'user', 'content': '审批测试'}]}, user)

    def test_default_mapping_precedes_model_reasoning_policy(self):
        result = self.prepare()
        self.assertEqual(result.payload['model'], 'deepseek-v4-flash')
        self.assertEqual(result.payload['reasoning_effort'], 'max')
        self.assertEqual(result.response_model, 'codex-auto-review')
        self.assertEqual(self.prepare('ordinary').payload['model'], 'ordinary')
        self.assertEqual(self.prepare('provider/codex-auto-review').payload['model'], 'codex-auto-review')

    async def test_environment_default_user_override_persistence_and_admin_field(self):
        with mock.patch.dict('os.environ', {KEY: 'glm-5.3', 'CODEBUDDY_DATA_DIR': config.get_data_dir()}):
            config.load_config()
            self.assertEqual(self.prepare().payload['model'], 'glm-5.3')
            user = AuthenticatedUser(username='alice', source='session_cookie')
            result = await save_admin_settings(AdminSettingsUpdate(settings={KEY: ' vendor/kimi-k3-1 '}), user)
            self.assertEqual(result['settings'][KEY], 'vendor/kimi-k3-1')
            field = next(field for field in (await get_admin_settings(user))['fields'] if field['key'] == KEY)
            self.assertEqual(field['type'], 'text')
            self.assertEqual(self.prepare(user=user).payload['model'], 'kimi-k3-1')
            self.assertEqual(self.prepare(user='bob').payload['model'], 'glm-5.3')
            config.load_config()
            self.assertEqual(self.prepare(user=user).payload['model'], 'kimi-k3-1')
            config.update_settings({'CODEBUDDY_STRIP_MODEL_NAMESPACE': False}, user=user)
            self.assertEqual(self.prepare(user=user).payload['model'], 'vendor/kimi-k3-1')

    def test_invalid_targets_fail_and_cannot_replace_existing_value(self):
        for value in ('', ' ', None, False, 1, [], 'a b', 'a\nb', 'a/', 'codex-auto-review', 'vendor/codex-auto-review'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                coerce_user_setting(KEY, value)
        config.update_settings({KEY: 'kimi-k3-1'}, username='alice')
        with self.assertRaises(ValueError):
            config.update_settings({KEY: ''}, username='alice')
        self.assertEqual(self.prepare(user='alice').payload['model'], 'kimi-k3-1')
        with mock.patch.dict('os.environ', {KEY: '', 'CODEBUDDY_DATA_DIR': config.get_data_dir()}):
            with self.assertRaises(ValueError):
                config.load_config()
