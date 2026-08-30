from v8help.search.chunker import chunk_text


def test_short_text_single_chunk():
    assert chunk_text("короткий текст") == ["короткий текст"]
    assert chunk_text("a" * 1500) == ["a" * 1500]


def test_long_text_splits_at_line_boundary():
    # 3 строки по 600 = 1800 > 1500 → 2 чанка, разрез по границе строки
    lines = ["x" * 600, "y" * 600, "z" * 600]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_size=1500, overlap=0)
    assert len(chunks) == 2
    # первый чанк не должен разрывать строку
    assert chunks[0] == "x" * 600 + "\n" + "y" * 600
    assert chunks[1] == "z" * 600
    # без перекрытия контент не теряется и не дублируется
    assert "\n".join(chunks) == text


def test_overlap_keeps_context():
    lines = ["a" * 700, "b" * 700, "c" * 700, "d" * 700]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) == 3
    # второй чанк начинается с хвоста первого (целая строка b)
    assert chunks[0] == "a" * 700 + "\n" + "b" * 700
    assert chunks[1] == "b" * 700 + "\n" + "c" * 700
    assert chunks[2] == "c" * 700 + "\n" + "d" * 700


def test_no_content_lost():
    import random

    rnd = random.Random(42)
    lines = ["".join(rnd.choice("абвгд ") for _ in range(rnd.randint(1, 80)))
             for _ in range(200)]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) > 1
    # каждая строка исходника присутствует хотя бы в одном чанке
    all_text = "\n".join(chunks)
    for ln in lines:
        assert ln in all_text


def test_single_line_longer_than_chunk():
    text = "q" * 4000
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) == 3
    assert all(len(c) <= 1500 for c in chunks)
    # перекрытие между частями длинной строки
    assert chunks[0] == "q" * 1400
    assert chunks[1] == "q" * 1500
    assert chunks[2] == "q" * 1500


def test_long_line_among_short():
    text = "\n".join(["s" * 100, "q" * 3000, "t" * 100])
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) >= 3
    assert chunks[0] == "s" * 100
    assert chunks[-1] == "t" * 100
    for c in chunks:
        assert len(c) <= 1500
