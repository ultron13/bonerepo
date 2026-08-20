"""Reading a file another process is still appending to."""

from plimsoll_agent.jtl import JtlReader

HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,success\n"


def test_a_complete_file_yields_every_sample() -> None:
    reader = JtlReader()
    rows = HEADER + "1787217508062,145,Browse,200,OK,true\n1787217508200,90,Checkout,200,OK,true\n"
    samples = list(reader.feed(rows))
    assert [s.label for s in samples] == ["Browse", "Checkout"]
    assert samples[0].elapsed == 145
    assert samples[0].at == 1787217508.062


def test_a_partial_line_is_held_until_it_completes() -> None:
    """JMeter appends as it goes, so a read lands mid-line. Parsing the half
    would invent a sample that never happened."""
    reader = JtlReader()
    assert list(reader.feed(HEADER + "1787217508062,145,Bro")) == []
    samples = list(reader.feed("wse,200,OK,true\n"))
    assert [s.label for s in samples] == ["Browse"]
    assert samples[0].elapsed == 145


def test_the_header_is_read_once_and_not_treated_as_a_sample() -> None:
    reader = JtlReader()
    assert list(reader.feed(HEADER)) == []
    assert [s.label for s in reader.feed("1787217508062,10,A,200,OK,true\n")] == ["A"]


def test_a_failed_sample_is_read_as_failed() -> None:
    reader = JtlReader()
    list(reader.feed(HEADER))
    sample = next(iter(reader.feed("1787217508062,3000,Checkout,500,Server Error,false\n")))
    assert sample.success is False
    assert sample.response_code == "500"
    assert sample.message == "Server Error"


def test_a_malformed_row_is_skipped_rather_than_guessed_at() -> None:
    """The complete JTL reaches object storage regardless, so dropping a torn
    row here loses nothing that cannot be recovered later."""
    reader = JtlReader()
    list(reader.feed(HEADER))
    samples = list(
        reader.feed("not-a-timestamp,10,A,200,OK,true\n1787217508062,20,B,200,OK,true\n")
    )
    assert [s.label for s in samples] == ["B"]


def test_columns_are_read_by_name_not_position() -> None:
    """The JMeter column set is configurable; a positional read would parse a
    reordered file into confident nonsense."""
    reader = JtlReader()
    list(reader.feed("label,success,elapsed,timeStamp\n"))
    sample = next(iter(reader.feed("Browse,true,42,1787217508062\n")))
    assert sample.label == "Browse"
    assert sample.elapsed == 42
