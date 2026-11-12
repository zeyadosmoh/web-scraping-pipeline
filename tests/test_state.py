from scraper.state import Checkpoint


def test_missing_checkpoint_starts_fresh(tmp_path):
    c = Checkpoint.load(tmp_path / "ckpt.json")
    assert c.done_urls == set() and c.next_list_url is None


def test_roundtrip_and_resume(tmp_path):
    p = tmp_path / "state" / "ckpt.json"
    c = Checkpoint.load(p)
    c.run_id, c.next_list_url = "run-1", "https://books.toscrape.com/catalogue/page-3.html"
    c.mark_done("u1"); c.mark_done("u2")
    c.save()
    # simulate a crash and restart
    r = Checkpoint.load(p)
    assert (r.run_id, r.next_list_url) == ("run-1", "https://books.toscrape.com/catalogue/page-3.html")
    assert r.is_done("u1") and r.is_done("u2") and not r.is_done("u3")
    assert not list(p.parent.glob(".ckpt-*"))  # no temp files left behind


def test_clear_removes_file(tmp_path):
    p = tmp_path / "ckpt.json"
    c = Checkpoint.load(p); c.mark_done("u1"); c.save()
    c.clear()
    assert not p.exists() and Checkpoint.load(p).done_urls == set()
