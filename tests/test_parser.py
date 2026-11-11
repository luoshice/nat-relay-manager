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


class NodeDisplayNameTests(unittest.TestCase):
    def setUp(self):
        self.state = nrm.migrate_state({
            "schema_version": 1,
            "public_address": "nat.example.com",
            "public_port": 53993,
            "listen_port": 10000,
            "server_name": "nat.example.com",
            "direct_user": {"name": "nat-direct", "uuid": "direct-uuid"},
            "upstreams": [
                {"name": "us-01", "server_name": "us.example.com"},
                {"name": "us-02", "server_name": "us2.example.com"},
            ],
        })

    def test_migration_adds_default_display_names(self):
        self.assertEqual(nrm.node_display_name(self.state, "direct"), "NAT-Direct")
        self.assertEqual(nrm.node_display_name(self.state, self.state["upstreams"][0]), "NAT-us-01")

    def test_rename_upstream_and_direct(self):
        self.assertEqual(nrm.set_node_display_name(self.state, "us-01", "美国洛杉矶 01"), ("us-01", "美国洛杉矶 01"))
        self.assertEqual(nrm.node_display_name(self.state, self.state["upstreams"][0]), "美国洛杉矶 01")
        nrm.set_node_display_name(self.state, "NAT-Direct", "香港 NAT 直连")
        self.assertEqual(nrm.node_display_name(self.state, "direct"), "香港 NAT 直连")

    def test_duplicate_display_names_are_rejected(self):
        nrm.set_node_display_name(self.state, "us-01", "美国线路")
        with self.assertRaises(nrm.NRMError):
            nrm.set_node_display_name(self.state, "us-02", "美国线路")

    def test_invalid_display_names_are_rejected(self):
        for value in ("", "   ", "bad" + chr(10) + "name", "x" * 65):
            with self.subTest(value=value), self.assertRaises(nrm.NRMError):
                nrm.validate_display_name(value)

    def test_technical_node_identifier_is_preserved(self):
        nrm.set_node_display_name(self.state, "us-01", "洛杉矶线路")
        self.assertEqual(self.state["upstreams"][0]["name"], "us-01")




class ServiceTemplateTests(unittest.TestCase):
    def test_systemd_unit_matches_singbox_lite_container_safe_model(self):
        unit = (Path(__file__).resolve().parents[1] / "systemd" / "nrm.service").read_text()
        self.assertIn("ExecStart=/usr/local/bin/sing-box run -c /etc/nrm/config.json", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("RestartSec=3s", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        directives = [line.strip() for line in unit.splitlines()
                       if line.strip() and not line.lstrip().startswith("#")]
        for forbidden in ("User=", "Group=", "CapabilityBoundingSet=",
                          "AmbientCapabilities=", "PrivateTmp=", "ProtectSystem="):
            self.assertFalse(any(line.startswith(forbidden) for line in directives), forbidden)

    def test_installer_uses_one_systemd_template_without_user_switch(self):
        installer = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertNotIn("useradd --system", installer)
        self.assertNotIn("systemd-detect-virt", installer)
        self.assertIn('SERVICE_TEMPLATE="$TMP_DIR/src/systemd/nrm.service"', installer)
        self.assertNotIn("nrm-container.service", installer)

    def test_permissions_remain_root_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "etc-nrm"
            base.mkdir()
            config = base / "config.json"
            config.write_text("{}")
            old_base = nrm.BASE
            try:
                nrm.BASE = base
                nrm.set_base_permissions()
                nrm.set_config_permissions(config)
                self.assertEqual(base.stat().st_mode & 0o777, 0o700)
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            finally:
                nrm.BASE = old_base




if __name__=="__main__": unittest.main()
