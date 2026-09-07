from pathlib import Path
import re
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import html


NS = {
    "q": "http://www.imsglobal.org/xsd/ims_qtiasiv1p2"
}

TYPE_NAMES = {
    "multiple_answers_question": "Множественный выбор",
    "multiple_choice_question": "Одиночный выбор",
    "matching_question": "Сопоставление",
    "numerical_question": "Числовой ответ",
    "essay_question": "Развёрнутый ответ",
    "file_upload_question": "Загрузка файла",
}


def clean_html(text):
    if not text:
        return ""

    text = html.unescape(text)
    soup = BeautifulSoup(text, "html.parser")

    for img in soup.find_all("img"):
        equation = (
            img.get("data-equation-content")
            or img.get("alt")
            or img.get("title")
        )

        if equation:
            img.replace_with(f"\\({equation}\\)")
        else:
            img.decompose()

    for br in soup.find_all("br"):
        br.replace_with("\n")

    return soup.get_text(" ", strip=True)


def get_metadata(item):
    return {
        field.findtext("q:fieldlabel", namespaces=NS):
            field.findtext("q:fieldentry", namespaces=NS)
        for field in item.findall(".//q:qtimetadatafield", NS)
    }


def get_document_title(root, source):
    """
    Пытается найти название задания/теста в XML.
    Если название не найдено, используется имя исходного файла.
    """

    # 1. Ищем bank_title
    for field in root.findall(".//q:qtimetadatafield", NS):
        label = field.findtext("q:fieldlabel", namespaces=NS)
        value = field.findtext("q:fieldentry", namespaces=NS)

        if label == "bank_title" and value:
            value = value.strip()

            if value and value.lower() != "неотвеченные вопросы":
                return value

    # 2. Ищем текстовые поля с потенциальным названием
    title_labels = {
        "title",
        "name",
        "quiz_title",
        "assessment_title",
        "test_title",
        "assignment_title",
    }

    for field in root.findall(".//q:qtimetadatafield", NS):
        label = field.findtext("q:fieldlabel", namespaces=NS)
        value = field.findtext("q:fieldentry", namespaces=NS)

        if label and label.lower() in title_labels and value:
            value = value.strip()

            if value:
                return value

    # 3. Если в XML есть <assessment title="...">
    for elem in root.iter():
        title = elem.attrib.get("title")

        if title and title.strip():
            # Не берём названия отдельных вопросов ("1", "1.f" и т.д.)
            if not re.fullmatch(r"\d+(?:\.[a-zA-Z]+)?", title.strip()):
                return title.strip()

    # 4. Запасной вариант — имя XML-файла
    name = source.name

    if name.lower().endswith(".xml.qti"):
        name = name[:-8]

    return name


def make_safe_filename(name):
    """
    Преобразует название в безопасное имя файла,
    сохраняя кириллицу.
    """

    name = name.strip()

    # Убираем запрещённые Windows-символы
    name = re.sub(r'[<>:"/\\|?*]', "_", name)

    # Убираем управляющие символы
    name = re.sub(r"[\x00-\x1f]", "", name)

    # Схлопываем пробелы
    name = re.sub(r"\s+", " ", name)

    # Windows не любит точку/пробел в конце имени
    name = name.rstrip(" .")

    if not name:
        name = "questions"

    return name


def get_question_text(item):
    materials = item.findall(
        "./q:presentation/q:material/q:mattext",
        NS,
    )

    return "\n".join(
        clean_html(material.text)
        for material in materials
        if material.text
    ).strip()


def get_choice_texts(item):
    choices = []

    for label in item.findall(
        ".//q:response_lid/q:render_choice/q:response_label",
        NS,
    ):
        materials = label.findall(".//q:mattext", NS)

        text = " ".join(
            clean_html(material.text)
            for material in materials
            if material.text
        ).strip()

        if text:
            choices.append(text)

    return choices


def convert_file(source):
    print(f"Обработка: {source.name}")

    text = source.read_text(
        encoding="utf-8",
        errors="replace",
    )

    root = ET.fromstring(text)
    items = root.findall(".//q:item", NS)

    document_title = get_document_title(root, source)
    safe_title = make_safe_filename(document_title)

    output = source.parent / f"{safe_title}.md"

    # Если вдруг название совпало с существующим файлом,
    # не затираем его молча.
    if output == source:
        output = source.parent / f"{safe_title}_converted.md"

    lines = [
        f"# {document_title}",
        "",
        f"Всего заданий: **{len(items)}**.",
        "",
    ]

    type_counts = {}
    choice_count = 0

    for number, item in enumerate(items, 1):
        metadata = get_metadata(item)
        question_type = metadata.get("question_type", "")
        title = item.get("title", f"{number}")

        type_counts[question_type] = (
            type_counts.get(question_type, 0) + 1
        )

        lines.extend([
            f"## {number}. {title}",
            "",
            f"**Тип:** "
            f"{TYPE_NAMES.get(question_type, question_type)}",
            "",
        ])

        question = get_question_text(item)

        if question:
            lines.extend([
                question,
                "",
            ])

        if question_type in (
            "multiple_answers_question",
            "multiple_choice_question",
        ):
            choices = get_choice_texts(item)
            choice_count += len(choices)

            for choice in choices:
                lines.append(f"- ☐ {choice}")

            lines.append("")

        elif question_type == "matching_question":
            for response in item.findall(
                "./q:presentation/q:response_lid",
                NS,
            ):
                prompt_materials = response.findall(
                    "./q:material/q:mattext",
                    NS,
                )

                prompt = " ".join(
                    clean_html(material.text)
                    for material in prompt_materials
                    if material.text
                ).strip()

                if prompt:
                    lines.extend([
                        f"**{prompt}**",
                        "",
                    ])

                choices = response.findall(
                    "./q:render_choice/q:response_label",
                    NS,
                )

                for index, choice in enumerate(choices, 1):
                    materials = choice.findall(
                        ".//q:mattext",
                        NS,
                    )

                    choice_text = " ".join(
                        clean_html(material.text)
                        for material in materials
                        if material.text
                    ).strip()

                    if choice_text:
                        lines.append(
                            f"- {index}. {choice_text}"
                        )

                lines.append("")

        elif question_type == "numerical_question":
            lines.extend([
                "**Ответ:** `________________`",
                "",
            ])

        elif question_type == "essay_question":
            lines.extend([
                "**Ответ:**",
                "",
                "```text",
                "",
                "```",
                "",
            ])

        elif question_type == "file_upload_question":
            lines.extend([
                "**Ответ:** прикрепить файл.",
                "",
            ])

    lines.extend([
        "---",
        "",
        "## Статистика",
        "",
    ])

    for question_type, count in type_counts.items():
        name = TYPE_NAMES.get(question_type, question_type)
        lines.append(f"- {name}: {count}")

    lines.extend([
        f"- Всего вариантов в вопросах с выбором: {choice_count}",
        "",
    ])

    output.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(
        f"  → {output.name} "
        f"({len(items)} заданий, {choice_count} вариантов)"
    )


def main():
    current_dir = Path.cwd()

    files = sorted(current_dir.glob("*.xml.qti"))

    if not files:
        print("Файлов *.xml.qti в текущей директории не найдено.")
        return

    print(f"Найдено файлов: {len(files)}")
    print()

    for source in files:
        try:
            convert_file(source)
        except Exception as error:
            print(
                f"  ОШИБКА при обработке {source.name}: "
                f"{error}"
            )

    print()
    print("Готово.")


if __name__ == "__main__":
    main()