"""CLI subcommands against the fake edge."""

from __future__ import annotations

import json

import pytest

from hardshell_telemetry.cli import main


@pytest.fixture
def corpus_file(tmp_path):
    def write(lines: list[dict]) -> str:
        path = tmp_path / "corpus.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        return str(path)

    return write


def connection(edge) -> list[str]:
    return ["--api-key", "test-key", "--base-url", edge.base_url]


class TestConfig:
    def test_missing_config_exits_2_naming_env_vars(self, capsys, monkeypatch):
        monkeypatch.delenv("HARDSHELL_API_KEY", raising=False)
        monkeypatch.delenv("HARDSHELL_BASE_URL", raising=False)
        assert main(["smoke-test"]) == 2
        err = capsys.readouterr().err
        assert "HARDSHELL_API_KEY" in err and "HARDSHELL_BASE_URL" in err

    def test_env_vars_used(self, edge, monkeypatch, capsys):
        monkeypatch.setenv("HARDSHELL_API_KEY", "env-key")
        monkeypatch.setenv("HARDSHELL_BASE_URL", edge.base_url)
        main(["report", "document-access", "--json"])
        assert edge.last.headers["Authorization"] == "Bearer env-key"

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert "hardshell-telemetry" in capsys.readouterr().out


class TestSmokeTest:
    def _arm_report(self, edge, run_id: str) -> None:
        doc_id = f"hardshell-smoke:{run_id}"
        chunk_ids = [f"{doc_id}:0", f"{doc_id}:1"]
        edge.respond(
            "GET",
            "/v1/reports/document-access",
            body={
                "documents": [
                    {
                        "document_id": doc_id,
                        "name": "smoke",
                        "chunk_count": 2,
                        "chunks": [{"chunk_id": c, "access_count": 1} for c in chunk_ids],
                    }
                ],
                "total_documents": 1,
            },
        )

    def test_full_pass(self, edge, capsys):
        self._arm_report(edge, "fixedrun")
        code = main(["smoke-test", *connection(edge), "--run-id", "fixedrun", "--json"])
        assert code == 0
        result = json.loads(capsys.readouterr().out)
        assert result["passed"] is True
        assert [s["name"] for s in result["steps"]] == ["config", "register", "record", "join"]
        # traffic labeled testing
        registered = [r for r in edge.requests if r.path == "/v1/documents"]
        assert registered[0].json["source"] == "testing"

    def test_join_failure_names_the_id_mismatch(self, edge, capsys):
        # report never contains the smoke ids -> join step fails with the hint
        code = main(
            [
                "smoke-test",
                *connection(edge),
                "--run-id",
                "fixedrun",
                "--poll-seconds",
                "0.2",
            ]
        )
        assert code == 1
        out = capsys.readouterr().out
        assert "[FAIL] join" in out
        assert "may not match the ids you register" in out

    def test_join_walks_report_pages(self, edge, capsys):
        # Smoke doc sits on page 2 of a busy tenant's report.
        doc_id = "hardshell-smoke:fixedrun"
        chunk_ids = [f"{doc_id}:0", f"{doc_id}:1"]
        filler = [
            {"document_id": f"other-{i}", "name": "", "chunk_count": 0, "chunks": []}
            for i in range(200)
        ]
        target = {
            "document_id": doc_id,
            "name": "smoke",
            "chunk_count": 2,
            "chunks": [{"chunk_id": c, "access_count": 1} for c in chunk_ids],
        }
        edge.respond_sequence(
            "GET",
            "/v1/reports/document-access",
            [
                {"documents": filler, "total_documents": 201},
                {"documents": [target], "total_documents": 201},
            ],
        )
        code = main(["smoke-test", *connection(edge), "--run-id", "fixedrun", "--json"])
        assert code == 0
        assert json.loads(capsys.readouterr().out)["passed"] is True
        report_calls = [r for r in edge.requests if r.path == "/v1/reports/document-access"]
        assert report_calls[1].query.get("offset") == ["200"]

    def test_auth_failure_hints_at_key(self, edge, capsys):
        edge.respond("POST", "/v1/documents", status=401, body={"detail": "bad key"})
        code = main(["smoke-test", *connection(edge), "--json"])
        assert code == 1
        result = json.loads(capsys.readouterr().out)
        register = next(s for s in result["steps"] if s["name"] == "register")
        assert not register["ok"]
        assert "HARDSHELL_API_KEY" in register["detail"]


class TestValidateCorpus:
    def test_clean_corpus_exit_0(self, corpus_file, capsys):
        path = corpus_file([{"id": "doc-1", "chunks": ["c-1", "c-2"]}])
        assert main(["validate-corpus", path]) == 0
        assert "SAFE" in capsys.readouterr().out

    def test_missing_ids_under_existing_exit_1(self, corpus_file, capsys):
        path = corpus_file([{"name": "no id", "chunks": ["c-1"]}])
        assert main(["validate-corpus", path, "--strategy", "existing"]) == 1
        assert "ERROR" in capsys.readouterr().out

    def test_strict_fails_on_warnings(self, corpus_file):
        path = corpus_file([{"content": "text only"}])
        assert main(["validate-corpus", path]) == 0
        assert main(["validate-corpus", path, "--strict"]) == 1

    def test_bad_jsonl_names_line(self, tmp_path, capsys):
        path = tmp_path / "bad.jsonl"
        path.write_text('{"id": "ok"}\nnot json\n', encoding="utf-8")
        assert main(["validate-corpus", str(path)]) == 2
        assert ":2:" in capsys.readouterr().err

    def test_unknown_strategy_exit_2(self, corpus_file, capsys):
        path = corpus_file([{"id": "doc-1"}])
        assert main(["validate-corpus", path, "--strategy", "nope"]) == 2
        assert "legacy:<uuid>" in capsys.readouterr().err

    def test_chunks_must_be_an_array(self, corpus_file, capsys):
        path = corpus_file([{"id": "doc-1", "chunks": {"c-1": True}}])
        assert main(["validate-corpus", path]) == 2
        assert "must be a JSON array" in capsys.readouterr().err

    def test_null_chunks_rejected_with_line_number(self, corpus_file, capsys):
        path = corpus_file([{"id": "doc-1", "chunks": None}])
        assert main(["validate-corpus", path]) == 2
        assert ":1:" in capsys.readouterr().err


class TestRegisterCorpus:
    def test_dry_run_sends_nothing(self, edge, corpus_file, capsys):
        path = corpus_file([{"id": "doc-1", "chunks": ["c-1"]}])
        code = main(["register-corpus", path, "--dry-run", *connection(edge), "--json"])
        assert code == 0
        assert json.loads(capsys.readouterr().out)["safe"] is True
        assert edge.requests == []

    def test_registers_documents_and_chunks_with_links(self, edge, corpus_file, capsys):
        edge.respond("POST", "/v1/documents", body={"documents_upserted": 2})
        edge.respond("POST", "/v1/chunks", body={"chunks_upserted": 3, "links_upserted": 3})
        path = corpus_file(
            [
                {"id": "doc-1", "name": "One", "chunks": ["c-1", "c-2"]},
                {"id": "doc-2", "chunks": [{"id": "c-3"}]},
            ]
        )
        code = main(["register-corpus", path, *connection(edge), "--json"])
        assert code == 0
        docs_request = next(r for r in edge.requests if r.path == "/v1/documents")
        assert [d["document_id"] for d in docs_request.json["documents"]] == ["doc-1", "doc-2"]
        assert docs_request.json["source"] == "index-build"
        chunks_request = next(r for r in edge.requests if r.path == "/v1/chunks")
        assert chunks_request.json["chunks"][0] == {
            "chunk_id": "c-1",
            "document_links": [{"document_id": "doc-1"}],
        }
        summary = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert summary == {"documents_registered": 2, "chunks_registered": 3}

    def test_id_changes_refused_without_flag(self, edge, corpus_file, capsys):
        path = corpus_file([{"id": "doc-1", "chunks": ["c-1"]}])
        code = main(["register-corpus", path, "--strategy", "derived", *connection(edge)])
        assert code == 1
        assert "--allow-id-changes" in capsys.readouterr().err
        assert edge.requests == []

    def test_id_changes_allowed_with_flag(self, edge, corpus_file):
        path = corpus_file([{"id": "doc-1", "chunks": ["c-1"]}])
        code = main(
            [
                "register-corpus",
                path,
                "--strategy",
                "derived",
                "--allow-id-changes",
                *connection(edge),
            ]
        )
        assert code == 0
        assert any(r.path == "/v1/documents" for r in edge.requests)

    def test_errors_abort_before_sending(self, edge, corpus_file, capsys):
        path = corpus_file([{"name": "no id", "chunks": ["c-1"]}])
        code = main(["register-corpus", path, "--strategy", "existing", *connection(edge)])
        assert code == 1
        assert "nothing was sent" in capsys.readouterr().err
        assert edge.requests == []

    @pytest.mark.parametrize("value", ["0", "-3"])
    def test_nonpositive_batch_size_rejected_at_parse(self, edge, corpus_file, value):
        path = corpus_file([{"id": "doc-1", "chunks": ["c-1"]}])
        with pytest.raises(SystemExit) as excinfo:
            main(["register-corpus", path, "--batch-size", value, *connection(edge)])
        assert excinfo.value.code == 2
        assert edge.requests == []

    @pytest.mark.parametrize("value", ["0", "-3", "nan", "inf"])
    def test_nonpositive_poll_seconds_rejected_at_parse(self, edge, value):
        with pytest.raises(SystemExit) as excinfo:
            main(["smoke-test", "--poll-seconds", value, *connection(edge)])
        assert excinfo.value.code == 2

    def test_batching_respects_batch_size(self, edge, corpus_file):
        path = corpus_file([{"id": "doc-1", "chunks": [f"c-{i}" for i in range(5)]}])
        main(["register-corpus", path, "--batch-size", "2", *connection(edge)])
        chunk_requests = [r for r in edge.requests if r.path == "/v1/chunks"]
        assert [len(r.json["chunks"]) for r in chunk_requests] == [2, 2, 1]


class TestReport:
    BODY = {
        "documents": [
            {
                "document_id": "doc-1",
                "name": "Handbook",
                "chunk_count": 2,
                "chunks": [
                    {"chunk_id": "c-1", "access_count": 3},
                    {"chunk_id": "c-2", "access_count": 0},
                ],
            }
        ],
        "total_documents": 41,
    }

    def test_human_output(self, edge, capsys):
        edge.respond("GET", "/v1/reports/document-access", body=self.BODY)
        assert main(["report", "document-access", *connection(edge)]) == 0
        out = capsys.readouterr().out
        assert "41 documents registered" in out
        assert "Handbook: 3 retrievals across 2 chunks" in out

    def test_json_output_and_window(self, edge, capsys):
        edge.respond("GET", "/v1/reports/document-access", body=self.BODY)
        assert main(["report", "document-access", "--days", "7", "--json", *connection(edge)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total_documents"] == 41
        assert payload["documents"][0]["accesses"] == 3
        assert "window_start" in edge.last.query
