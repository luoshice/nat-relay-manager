import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nrm


class VlessParserTests(unittest.TestCase):
    def setUp(self):
        self.valid = ("vless://123e4567-e89b-12d3-a456-426614174000@example.net:443"
            "?encryption=none&security=reality&sni=www.example.com&fp=chrome"
            "&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&sid=0123abcd"
            "&type=tcp&flow=xtls-rprx-vision#US-01")

    def test_valid_reality_tcp(self):
        parsed=nrm.parse_vless_reality(self.valid)
        self.assertEqual(parsed["server"],"example.net")
        self.assertEqual(parsed["server_port"],443)
        self.assertEqual(parsed["flow"],"xtls-rprx-vision")
        self.assertEqual(parsed["label"],"US-01")
        self.assertEqual(parsed["server_name"],"www.example.com")

    def test_reject_ws(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("type=tcp","type=ws"))

    def test_reject_non_reality(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("security=reality","security=tls"))

    def test_reject_bad_flow(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("xtls-rprx-vision","xtls-rprx-splice"))

    def test_reject_invalid_uuid(self):
        with self.assertRaises(nrm.NRMError): nrm.parse_vless_reality(self.valid.replace("123e4567-e89b-12d3-a456-426614174000","not-a-uuid"))

    def test_short_id_up_to_8_bytes(self):
        self.assertEqual(nrm.parse_vless_reality(self.valid.replace("0123abcd","0123456789abcdef"))["short_id"],"0123456789abcdef")


class StateMigrationTests(unittest.TestCase):
    def test_defaults_added_to_existing_state(self):
        state={"schema_version":1,"public_address":"nat.example.com","public_port":53993,"listen_port":10000,
               "server_name":"nat-sni.example.com","upstreams":[{"name":"us-01","server_name":"us-sni.example.com"}]}
        migrated=nrm.migrate_state(state)
        self.assertEqual(migrated["handshake_server"],"nat-sni.example.com")
        self.assertEqual(migrated["upstreams"][0]["source_server_name"],"us-sni.example.com")
        self.assertFalse(migrated["upstreams"][0]["server_name_override"])
        self.assertEqual(migrated["port_mappings"][0]["external_port"],53993)

    def test_preserve_different_handshake_and_existing_rule(self):
        state={"schema_version":1,"public_address":"nat.example.com","public_port":53993,"listen_port":10000,
               "server_name":"in.example.com","handshake_server":"other.example.net",
               "port_mappings":[{"name":"nrm-proxy","external_port":54000,"internal_port":10000}],"upstreams":[]}
        migrated=nrm.migrate_state(state)
        self.assertEqual(migrated["handshake_server"],"other.example.net")
        self.assertEqual(migrated["port_mappings"][0]["external_port"],54000)


class PortMappingTests(unittest.TestCase):
    def test_tcp_mapping(self):
        item=nrm.make_port_mapping("SSH",46013,22,"SSH")
        self.assertEqual(item["name"],"ssh")
        self.assertEqual(item["protocol"],"TCP")
        self.assertEqual(item["internal_port"],22)

    def test_reserved_name_and_invalid_port(self):
        with self.assertRaises(nrm.NRMError): nrm.make_port_mapping("nrm-proxy",53993,10000)
        with self.assertRaises(nrm.NRMError): nrm.make_port_mapping("web",70000,8080)


class ConfigBuilderTests(unittest.TestCase):
    def test_single_entry_tcp_only_and_independent_handshake(self):
        state={"listen_address":"0.0.0.0","listen_port":10000,"server_name":"in.example.com",
          "handshake_server":"hs.example.net","handshake_port":443,"private_key":"private-key","short_id":"0123abcd",
          "direct_user":{"name":"nat-direct","uuid":"direct-uuid"},
          "upstreams":[{"name":"us-01","user_name":"nat-us-01","client_uuid":"client-uuid","enabled":True,
          "tag":"upstream-us-01","server":"example.net","server_port":443,"uuid":"upstream-uuid",
          "flow":"xtls-rprx-vision","server_name":"up.example.org","public_key":"public-key",
          "short_id":"abcd1234","fingerprint":"chrome"}]}
        config=nrm.build_config(state)
        inbound=config["inbounds"][0]
        self.assertEqual(inbound["listen"],"0.0.0.0")
        self.assertEqual(inbound["tls"]["server_name"],"in.example.com")
        self.assertEqual(inbound["tls"]["reality"]["handshake"]["server"],"hs.example.net")
        outbound=next(x for x in config["outbounds"] if x["tag"]=="upstream-us-01")
        self.assertEqual(outbound["network"],"tcp")
        self.assertEqual(outbound["packet_encoding"],"")
        self.assertEqual(outbound["tls"]["server_name"],"up.example.org")
        self.assertEqual(config["route"]["final"],"block")
        self.assertEqual(config["route"]["rules"][0]["network"],["udp"])


if __name__=="__main__": unittest.main()
