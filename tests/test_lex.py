from v8help.lex import expand, split_pascal


def test_split_pascal_cyrillic():
    assert split_pascal("ПолучитьДатуНачала") == ["Получить", "Дату", "Начала"]
    assert split_pascal("СтрНайтиПоРегулярномуВыражению") == [
        "Стр", "Найти", "По", "Регулярному", "Выражению",
    ]


def test_split_pascal_acronyms():
    assert split_pascal("ИспользоватьSSL") == ["Использовать", "SSL"]
    assert split_pascal("XMLParser") == ["XML", "Parser"]


def test_split_pascal_no_boundary():
    assert split_pascal("Строка") == ["Строка"]
    assert split_pascal("SSL") == ["SSL"]
    assert split_pascal("") == []


def test_expand_keeps_original():
    text = "Метод СтрНайтиПоРегулярномуВыражению выполняет поиск."
    out = expand(text)
    assert "СтрНайтиПоРегулярномуВыражению" in out
    assert "Стр Найти По Регулярному Выражению" in out
