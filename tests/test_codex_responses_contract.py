"""固定版本 Codex CLI 的真实 HTTP 契约；显式启用后在临时目录执行工具。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.codebuddy_events import CodeBuddyResponseEvent
from src.responses_request import translate_responses_request
from src.responses_response import ResponsesAdapter


@unittest.skipUnless(os.environ.get('CODEBUDDY_TEST_CODEX') == '1', '设置 CODEBUDDY_TEST_CODEX=1 运行 Codex CLI 契约')
class CodexResponsesContractTests(unittest.TestCase):
    def test_patch_command_image_resume_and_compaction(self):
        executable = shutil.which('codex')
        self.assertIsNotNone(executable, '请安装 @openai/codex@0.153.4')
        version = subprocess.run([executable, '--version'], capture_output=True, text=True, check=True)
        self.assertEqual(version.stdout.strip(), 'codex-cli 0.153.4')
        requests, failures = [], []
        stage = 0
        compacted = False
        patch = '*** Begin Patch\n*** Add File: result.txt\n+契约验证\n*** End Patch'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                nonlocal stage, compacted
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    requests.append((self.path, body))
                    payload, bindings = translate_responses_request(body)
                    if not body.get('tools'):
                        compacted = True
                        delta = {'content': '已创建 result.txt 并执行验证命令。继续完成任务。'}
                        finish = 'stop'
                    elif stage < 5:
                        name = ('apply_patch', 'exec_command', 'view_image', 'cb2a_tool_search', 'echo')[stage]
                        binding = next((value for value in bindings.values() if value.name == name), None)
                        if binding is None:
                            raise AssertionError((stage, name, [(value.kind, value.name, value.namespace) for value in bindings.values()]))
                        args = ({'input': patch}, {'cmd': 'cat result.txt', 'max_output_tokens': 1000}, {'path': str(image)}, {'query': 'contract echo', 'limit': 1}, {'text': 'MCP_RESULT_731'})[stage]
                        delta = {'tool_calls': [{'index': 0, 'id': f'original_call_{stage}', 'type': 'function',
                                                'function': {'name': binding.alias, 'arguments': json.dumps(args, ensure_ascii=False)}}]}
                        finish = 'tool_calls'
                        stage += 1
                    else:
                        delta, finish = {'content': '契约完成'}, 'stop'
                    adapter = ResponsesAdapter(payload['model'], bindings)
                    state = adapter.create_stream_state()
                    delta['function_call'] = {'name': '', 'arguments': ''}
                    wire = adapter.process_stream_event(state, CodeBuddyResponseEvent.parse({
                        'choices': [{'delta': delta, 'finish_reason': finish}],
                        'usage': {'prompt_tokens': 200000 if stage == 5 and not compacted else 10, 'completion_tokens': 5}}))
                    wire += adapter.finalize_stream(state, True)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(''.join(wire).encode())
                except Exception as error:
                    failures.append(repr(error))
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"contract fixture failed"}}')

        with tempfile.TemporaryDirectory(prefix='codebuddy-codex-') as directory:
            root = Path(directory)
            home = root / 'home'
            home.mkdir()
            work = root / 'work'
            work.mkdir()
            image = work / 'image.png'
            image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKElEQVR4nO3NsQ0AAAzCMP5/un0CNkuZ41wybXsHAAAAAAAAAAAAxR4yw/wuPL6QkAAAAABJRU5ErkJggg=='))
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            mcp = root / 'mcp.py'
            mcp.write_text("""import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request.get('method')
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'contract', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'echo', 'description': '回显契约文本', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}}]}
    elif method == 'tools/call':
        result = {'content': [{'type': 'text', 'text': request['params']['arguments']['text']}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
""")
            catalog = Path(__file__).resolve().parents[1] / 'doc' / 'Codex模型目录示例.json'
            config = f'''model = "kimi-k3-1"
model_provider = "contract"
model_catalog_json = {json.dumps(str(catalog))}
model_context_window = 262144
model_auto_compact_token_limit = 100000
web_search = "disabled"
approval_policy = "never"
sandbox_mode = "workspace-write"
[model_providers.contract]
name = "本地契约"
base_url = "http://127.0.0.1:{server.server_port}/openai/v1"
env_key = "CODEBUDDY_CONTRACT_KEY"
wire_api = "responses"
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
[mcp_servers.contract]
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(mcp))}]
[mcp_servers.contract.tools.echo]
approval_mode = "approve"
'''
            (home / 'config.toml').write_text(config)
            env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'HOME') if key in os.environ}
            env.update(CODEX_HOME=str(home), CODEBUDDY_CONTRACT_KEY='synthetic-contract-key')
            try:
                first = subprocess.run([executable, 'exec', '--skip-git-repo-check', '--json', '-C', str(work),
                                        '--image', str(image), '--', '创建 result.txt，执行 cat 验证，然后报告完成。'],
                                       env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(failures, [])
                self.assertEqual(first.returncode, 0, first.stderr[-3000:])
                self.assertEqual((work / 'result.txt').read_text(), '契约验证\n')
                events = [json.loads(line) for line in first.stdout.splitlines() if line.startswith('{')]
                thread_id = next(event['thread_id'] for event in events if event['type'] == 'thread.started')
                resumed = subprocess.run([executable, 'exec', 'resume', '--skip-git-repo-check', '--json', thread_id, '继续报告完成。'],
                                         cwd=work, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(resumed.returncode, 0, resumed.stderr[-3000:])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(stage, 5)
        self.assertTrue(compacted, '必须观察到客户端压缩请求')
        self.assertTrue(all(path == '/openai/v1/responses' for path, _ in requests))
        items = [item for _, body in requests for item in body['input']]
        results = {item['call_id']: item for item in items if item.get('type') in (
            'custom_tool_call_output', 'function_call_output', 'tool_search_output')}
        self.assertEqual(results['original_call_0']['type'], 'custom_tool_call_output')
        self.assertIn('契约验证', json.dumps(results['original_call_1'], ensure_ascii=False))
        self.assertTrue(any(part.get('type') == 'input_image' for part in results['original_call_2']['output']))
        self.assertEqual(results['original_call_3']['tools'][0]['type'], 'namespace')
        self.assertIn('MCP_RESULT_731', json.dumps(results['original_call_4']))
        self.assertTrue(any(part.get('type') == 'input_image' for item in requests[0][1]['input']
                            for part in item.get('content', []) if isinstance(part, dict)))
