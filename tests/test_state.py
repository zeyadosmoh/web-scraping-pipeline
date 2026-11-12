from scraper.books import RawBook
from scraper.state import Checkpoint


def raw(url):
    return RawBook(url, "T", "£1.00", "In stock (1 available)", "One", "Poetry", "abcdef123456")


def test_missing_checkpoint_starts_fresh(tmp_path):
    c = Checkpoint.load(tmp_path / "ckpt.json")
    assert not c.exists and c.done_urls == set() and c.next_list_url is None


def test_roundtrip_and_resume_with_staged_rows(tmp_path):
    p = tmp_path / "state" / "checkpoint.json"
    c = Checkpoint.load(p)
    c.run_id, c.next_list_url = "run-1", "https://books.toscrape.com/catalogue/page-3.html"
    for u in ("u1", "u2"):
        c.stage(raw(u)); c.mark_done(u)
    c.failures.append({"url": "u9", "reason": "timeout"})
    c.save()
    c.stage(raw("u3"))  # crash after staging but before mark_done/save
    r = Checkpoint.load(p)
    assert r.exists and r.next_list_url.endswith("page-3.html")
    assert r.is_done("u1") and not r.is_done("u3")
    assert [x.url for x in r.staged_rows()] == ["u1", "u2"]  # torn row ignored
    assert r.failures == [{"url": "u9", "reason": "timeout"}]
    assert not list(p.parent.glob(".ckpt-*"))


def test_clear_removes_files(tmp_path):
    p = tmp_path / "checkpoint.json"
    c = Checkpoint.load(p); c.run_id = "r"; c.stage(raw("u1")); c.mark_done("u1"); c.save()
    c.clear()
    assert not p.exists() and not c.staging_path.exists() and not Checkpoint.load(p).exists


def test_in_memory_checkpoint_writes_nothing(tmp_path):
    c = Checkpoint.load(None)
    c.stage(raw("u1")); c.mark_done("u1"); c.save()
    assert c.staged_rows() == [] and list(tmp_path.iterdir()) == []
