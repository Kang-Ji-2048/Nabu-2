"""Run after `pip install .`; all data here is synthetic and no API key is used."""

from nabu import Reviewer, Viewer
from nabu.demo_data import generate


def main():
    viewer = Viewer(tenant="demo", scopes=("payments",))
    with Reviewer(provider="offline") as reviewer:
        reviewer.load(generate())
        report = reviewer.review(
            "demo:PAY-111", viewer=viewer,
            agent_scopes=("payments", "platform"),
            as_of="2023-10-04T11:08:10.105835Z",
        )
        print(report.to_markdown())
        run = reviewer.start_run(viewer=viewer, agent_scopes=("payments", "platform"))
        print(reviewer.resume(run, viewer=viewer, stop_after=5))
        result = reviewer.resume(run, viewer=viewer)
        assert result.status == "finished" and result.reviews == 10
        print(result)
        print(reviewer.evaluate(run, viewer=viewer, after="2023-06-01T00:00:00Z"))


if __name__ == "__main__":
    main()
