import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from boxscan import sprites

from test_raid import mega_reference

PNG = sprites.PNG + b"fake image"


class SpriteTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_names_follow_the_source(self):
        self.assertEqual(sprites.file_name(6, "megax", True), "6-megax.s.png")
        self.assertEqual((sprites.suffix("Galarian"), sprites.suffix("Hisuian"), sprites.suffix("Therian")), ("galar", "hisui", "therian"))
        self.assertEqual((sprites.suffix("Normal"), sprites.suffix("Incarnate")), (None, None))  # the plain sprite
        self.assertEqual(sprites.suffix("Alola", "MEGA_Y"), "megay")  # a mega form wins over the regular form
        for bad in ("../box.sqlite3", "1.png.exe", "1-a_b.png", "pm1.icon.png"):
            self.assertIsNone(sprites.NAME.fullmatch(bad))
        data = mega_reference()  # species 2 is "Wave" in the game master names
        self.assertEqual(sprites.url(data, "2-mega.s.png"), sprites.SHINY_URL + "wave-mega.png")
        self.assertEqual(sprites.url(data, "2.png"), sprites.BASE_URL + "wave.png")
        self.assertIsNone(sprites.url(data, "999.png"))

    def test_wanted_covers_box_forms_shiny_megas_and_bosses(self):
        entries = [{"dex": 2, "shiny": True}, {"dex": 2, "form": "Galarian"}, {"dex": 2, "shadow": True}, {"species": "x"}]
        bosses = [{"pokemon_id": 1, "temp": "MEGA"}, {"dex": 3}]
        names = sprites.wanted(entries, mega_reference(), bosses)
        self.assertEqual(names, ["2.png", "2.s.png", "2-mega.png", "2-galar.png", "1-mega.png", "1.png", "3.png"])

    def test_download_saves_remembers_missing_and_serves_fallbacks(self):
        asked = []

        data = mega_reference()

        def fetch(url):
            asked.append(url)
            if "-galar" in url:
                return None  # the source has no such file
            if "/flame" in url:
                raise OSError("offline")
            return PNG
        names = ["2.png", "2.s.png", "2-galar.png", "1.png", "999.png", "../evil.png"]
        result = sprites.download(self.root, names, data, fetch=fetch, workers=2)
        self.assertEqual((result["saved"], result["not_found"], result["failed"]), (2, 2, 1))  # 999 has no English name
        self.assertEqual(len(asked), 4)  # only names in the pattern with a species are asked for
        self.assertIn(sprites.SHINY_URL + "wave.png", asked)
        self.assertEqual(sprites.missing(self.root, names[:5]), ["1.png"])  # a 404 is not asked again
        self.assertEqual(sprites.find(self.root, 2, "galar", True).name, "2.s.png")  # no form: shiny plain
        self.assertEqual(sprites.find(self.root, 2, "galar").name, "2.png")
        self.assertIsNone(sprites.find(self.root, 1))
        status = sprites.status(self.root, names[:4])
        self.assertEqual((status["stored"], status["missing"], status["wanted"]), (2, 1, 4))
        asked.clear()
        sprites.download(self.root, names[:3], data, fetch=fetch)
        self.assertEqual(asked, [])  # nothing left to fetch

    def test_fetch_rejects_files_that_are_not_images(self):
        class Response:
            def __init__(self, data):
                self.data = data

            def read(self, limit):
                return self.data[:limit]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        with mock.patch("urllib.request.urlopen", return_value=Response(b"<html>")):
            with self.assertRaises(ValueError):
                sprites._fetch(sprites.BASE_URL + "wave.png")
        with mock.patch("urllib.request.urlopen", return_value=Response(PNG)):
            self.assertEqual(sprites._fetch(sprites.BASE_URL + "wave.png"), PNG)


class SpriteApiTests(unittest.TestCase):
    def test_images_are_served_only_after_the_user_downloads_them(self):
        import json
        from fastapi.testclient import TestClient
        from boxscan import reference as reference_module
        from boxscan.api import create_app
        from boxscan.box import BoxStore
        with TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            folder = data_dir / "reference" / "t"
            folder.mkdir(parents=True)
            (folder / "reference.json").write_text(json.dumps(reference_module.to_json(mega_reference())), encoding="utf-8")
            (data_dir / "reference" / "current").write_text("t", encoding="utf-8")
            BoxStore(data_dir).approve("scan", 1, {"species": "물몬", "dex": 2, "shiny": True}, {"scan_id": "scan", "individual": 1})
            client = TestClient(create_app(data_dir), base_url="http://localhost")
            self.assertEqual(client.get("/api/sprite", params={"dex": 2}).status_code, 404)
            status = client.get("/api/sprites").json()
            self.assertEqual((status["stored"], status["missing"]), (0, 3))  # plain, shiny, mega
            with mock.patch.object(sprites, "_fetch", return_value=PNG) as fetch:
                result = client.post("/api/sprites/download").json()
                self.assertEqual(fetch.call_count, 3)
            self.assertEqual((result["saved"], result["missing"]), (3, 0))
            image = client.get("/api/sprite", params={"dex": 2, "shiny": "1"})
            self.assertEqual((image.status_code, image.headers["content-type"]), (200, "image/png"))
            self.assertEqual(image.content, PNG)
            self.assertEqual(client.get("/api/sprite", params={"dex": 2, "temp": "MEGA"}).status_code, 200)
            self.assertEqual(client.post("/api/sprites/download", headers={"Origin": "http://evil.test"}).status_code, 403)
            entry = client.get("/api/pokemon").json()["pokemon"][0]
            self.assertEqual(entry["types"], ["WATER"])
            client.close()


if __name__ == "__main__":
    unittest.main()
