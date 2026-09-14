"""验证可视化使用真实字节边界，跨 token 的文字仍可完整重建。"""

import base64
import json
import unittest
from unittest import mock

from tokenizers import AddedToken, Tokenizer, decoders, models, normalizers, pre_tokenizers

from src.tokenizer_engine import TokenizerEngine, TokenizerError
from tests.test_tokenizer import tokenizer_files


def byte_files():
    values = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    alphabet = {value: chr(value) for value in values}
    for value in range(256):
        if value not in alphabet:
            alphabet[value] = chr(256 + len(alphabet) - len(values))
    vocab = {alphabet[value]: value for value in range(256)}
    pair = (alphabet[0xE4], alphabet[0xBD])
    vocab[''.join(pair)] = 256
    tokenizer = Tokenizer(models.BPE(vocab, [pair]))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.normalizer = normalizers.NFC()
    tokenizer.add_special_tokens([AddedToken('<测试>', special=True)])
    return {'tokenizer.json': tokenizer.to_str().encode()}


class TokenizerVisualizationTests(unittest.TestCase):
    def test_bytelevel_preserves_two_to_one_split_and_special_tokens(self):
        engine = TokenizerEngine(byte_files(), {'encoder': 'auto'})
        text = '你🙂\n<测试>'
        result = engine.encode_text(text)
        self.assertEqual(result['text'], text)
        self.assertEqual(result['input_tokens'], engine.count_text(text))
        self.assertEqual(result['tokens'][:2], [
            {'id': 256, 'start': 0, 'end': 2},
            {'id': 160, 'start': 2, 'end': 3},
        ])
        self.assertEqual(result['tokens'][-1], {'id': 257, 'start': 8, 'end': 16})
        self.assertEqual(engine.encode_text(''), {'text': '', 'input_tokens': 0, 'tokens': []})

    def test_bytelevel_displays_normalized_text_and_rejects_invalid_bytes(self):
        engine = TokenizerEngine(byte_files(), {'encoder': 'auto'})
        self.assertEqual(engine.encode_text('e\u0301')['text'], 'é')
        with mock.patch.object(engine, 'hf') as hf:
            hf.decoder = decoders.ByteLevel()
            hf.get_added_tokens_decoder.return_value = {}
            hf.encode.return_value.ids = [1]
            for token in ('ä', '你'):
                hf.encode.return_value.tokens = [token]
                with self.assertRaisesRegex(TokenizerError, '字节'):
                    engine.encode_text('你')

    def test_kimi_preserves_raw_bytes_and_empty_input(self):
        ranks = [bytes([value]) for value in range(256)] + [bytes.fromhex('e4bd')]
        files = {'tiktoken.model': b'\n'.join(
            base64.b64encode(value) + b' ' + str(index).encode()
            for index, value in enumerate(ranks)
        )}
        engine = TokenizerEngine(files, {'encoder': 'auto', 'format': 'kimi'})
        self.assertEqual(engine.encode_text('你'), {
            'text': '你', 'input_tokens': 2,
            'tokens': [{'id': 256, 'start': 0, 'end': 2}, {'id': 160, 'start': 2, 'end': 3}],
        })
        self.assertEqual(engine.encode_text('')['tokens'], [])

    def test_other_tokenizers_keep_original_offsets_and_unencoded_whitespace(self):
        files = tokenizer_files()
        data = json.loads(files['tokenizer.json'])
        data['normalizer'] = {'type': 'Lowercase'}
        files['tokenizer.json'] = json.dumps(data).encode()
        engine = TokenizerEngine(files, {'encoder': 'auto'})
        self.assertEqual(engine.encode_text('HELLO  你好\n'), {
            'text': 'HELLO  你好\n', 'input_tokens': 2,
            'tokens': [{'id': 1, 'start': 0, 'end': 5}, {'id': 3, 'start': 7, 'end': 13}],
        })
        self.assertEqual(engine.encode_text('  ')['tokens'], [])
        with mock.patch.object(engine, 'hf') as hf:
            hf.encode.return_value.ids = [1, 2]
            hf.encode.return_value.offsets = [(0, 1), (0, 1)]
            with self.assertRaisesRegex(TokenizerError, '精确'):
                engine.encode_text('你')

    def test_non_text_is_rejected(self):
        engine = TokenizerEngine(tokenizer_files(), {'encoder': 'auto'})
        with self.assertRaisesRegex(TokenizerError, '字符串'):
            engine.encode_text(42)
