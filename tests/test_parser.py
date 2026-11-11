import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nrm


class VlessParserTests(unittest.TestCase):
    def setUp(self):
        self.valid = (
            "vless://123e4567-e89b-12d3-a456-426614174000@example.net:443"
            "?encryption=none&security=reality&sni=www.example.com&fp=chrome"
            "&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&sid=0123abcd"
            "&type=tcp&flow=xtls-rprx-vision#US-01"
        )

    def test_valid_reality_tcp(self):
        parsed = nrm.parse_vless_reality(self.valid)
        self.assertEqual(parsed["server"], "example.net")
        self.assertEqual(parsed["server_port"], 443)
        self.assertEqual(parsed["flow"], "xtls-rprx-vision")
        self.assertEqual(parsed["label"], "US-01")

    def test_reject_ws(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("type=tcp", "type=ws"))

    def test_reject_non_reality(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("security=reality", "security=tls"))

    def test_reject_bad_flow(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("xtls-rprx-vision", "xtls-rprx-splice"))

    def test_reject_invalid_uuid(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("123e4567-e89b-12d3-a456-426614174000", "not-a-uuid"))


class ConfigBuilderTests(unittest.TestCase):
    def test_one_port_multi_user_tcp_only_and_fail_closed(self):
        state = {
            "listen_port": 10000, "server_name": "www.example.com", "handshake_server": "www.example.com",
            "handshake_port": 443, "private_key": "private-key", "short_id": "0123abcd",
            "direct_user": {"name": "nat-direct", "uuid": "direct-uuid"},
            "upstreams": [{"name": "us-01", "user_name": "nat-us-01", "client_uuid": "client-uuid", "enabled": True,
                "tag": "upstream-us-01", "server": "example.net", "server_port": 443, "uuid": "upstream-uuid",
                "flow": "xtls-rprx-vision", "server_name": "www.example.net", "public_key": "public-key",
                "short_id": "abcd1234", "fingerprint": "chrome"}]
        }
        config = nrm.build_config(state)
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["listen_port"], 10000)
        self.assertEqual(len(inbound["users"]), 2)
        upstream = next(o for o in config["outbounds"] if o["tag"] == "upstream-us-01")
        self.assertEqual(upstream["network"], "tcp")
        self.assertEqual(upstream["packet_encoding"], "")
        self.assertEqual(config["route"]["final"], "block")
        self.assertEqual(config["route"]["rules"][0]["network"], ["udp"])


if __name__ == "__main__":
    unittest.main()
