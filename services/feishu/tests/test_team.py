import time

from xirra import team


class FakePanel:
    def episodes(self, **_):
        now = int(time.time() * 1e9)
        return [
            {"station": "yam_2", "task": "insert_temperature_probe", "operator": "jianping_yin", "judged_by": "ningma", "start_unix_ns": now},
            {"station": "yam_abc", "task": "stack_bowls", "operator": "plan", "judged_by": "plan", "start_unix_ns": now - 10**9},
        ]

    def plans(self):
        return []

    def tasks(self):
        return [{"task": {"name": "insert_temperature_probe", "instruction": "Insert the probe."}}]

    def people(self):
        return [{"name": "jianping_yin"}, {"name": "ningma"}]


def site(tmp_path):
    (tmp_path / "robots" / "yam_2").mkdir(parents=True)
    (tmp_path / "robots" / "yam_2" / "station.toml").write_text("# Station yam_2 on machine ubuntu2: two YAM pairs.\n")
    (tmp_path / "robots" / "_archive" / "yam_abc").mkdir(parents=True)
    (tmp_path / "robots" / "_template").mkdir()
    return tmp_path


def test_stations_and_retired_come_from_robots(tmp_path):
    root = site(tmp_path)
    found = team.stations(root)
    assert set(found) == {"yam_2", "yam_abc"}
    assert found["yam_2"]["about"] == "Station yam_2 on machine ubuntu2: two YAM pairs"
    assert team.retired(root) == {"yam_abc"}


def test_the_team_text_says_who_did_what_where(tmp_path):
    text = team.Team(FakePanel(), site(tmp_path), lambda name: "任务概括：把测温棒插到测温枪头上\n更多").text()
    assert "insert_temperature_probe：把测温棒插到测温枪头上" in text
    assert "yam_abc：已退役" in text and "1 条没记采集员是谁" in text
    assert "jianping_yin：采集 1 条" in text and "ningma：判断 1 条" in text
