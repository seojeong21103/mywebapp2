import os
import re
import json
import math
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# =========================================================
# 기본 설정
# =========================================================

st.set_page_config(
    page_title="대한민국 인구구조 지도",
    page_icon="🗺️",
    layout="wide",
)

GEOJSON_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    "ver20260701/HangJeongDong_ver20260701.geojson"
)

# 고령화율 색 구간 - 연도가 바뀌어도 절대 변하지 않음
AGING_BREAKS = [19, 23, 28, 38]

# 유소년율은 고령화율보다 낮기 때문에 별도의 구간 사용
YOUTH_BREAKS = [5, 7, 9, 11]

# 행정구역 코드 변경
CODE_REPLACEMENTS = {
    "42": "51",       # 강원 → 강원특별자치도
    "45": "52",       # 전북 → 전북특별자치도
    "47720": "27720", # 군위군 → 대구광역시 군위군
}

# 시도 코드 이름.
# 실제 GeoJSON의 adm_nm에서 얻을 수 있으면 그 이름을 우선 사용한다.
SIDO_FALLBACK = {
    "11": "서울특별시",
    "26": "부산광역시",
    "27": "대구광역시",
    "28": "인천광역시",
    "29": "광주광역시",
    "30": "대전광역시",
    "31": "울산광역시",
    "36": "세종특별자치시",
    "41": "경기도",
    "43": "충청북도",
    "44": "충청남도",
    "46": "전라남도",
    "47": "경상북도",
    "48": "경상남도",
    "50": "제주특별자치도",
    "51": "강원특별자치도",
    "52": "전북특별자치도",
}


# =========================================================
# 스타일
# =========================================================

st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }

    .sub-title {
        color: #666;
        margin-bottom: 1rem;
    }

    .kpi-card {
        border: 1px solid #e5e7eb;
        border-radius: 16px;
        padding: 18px 20px;
        background: white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        min-height: 125px;
    }

    .kpi-title {
        color: #777;
        font-size: 0.9rem;
        margin-bottom: 8px;
    }

    .kpi-value {
        font-size: 1.65rem;
        font-weight: 800;
        color: #222;
    }

    .kpi-sub {
        color: #777;
        font-size: 0.82rem;
        margin-top: 5px;
    }

    .notice {
        background: #f7f7f7;
        border-radius: 12px;
        padding: 12px 16px;
        color: #555;
        font-size: 0.9rem;
        margin-top: 8px;
    }

    .source-box {
        color: #777;
        font-size: 0.78rem;
        margin-top: 18px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 유틸
# =========================================================

def clean_number(value):
    """문자열 숫자를 안전하게 숫자로 변환."""
    if pd.isna(value):
        return 0

    s = str(value).strip()
    s = s.replace(",", "")
    s = s.replace(" ", "")

    if s in ("", "-", "nan", "None"):
        return 0

    try:
        return float(s)
    except Exception:
        return 0


def extract_code(text):
    """
    행정구역 문자열에서 10자리 행정기관코드를 찾는다.

    예:
    서울특별시 종로구 (1111000000)
    → 1111000000
    """
    if pd.isna(text):
        return ""

    text = str(text)

    matches = re.findall(r"\d{10}", text)

    if matches:
        return matches[-1]

    return ""


def normalize_code(code):
    """옛 행정구역 코드를 현재 경계 코드 체계에 맞춘다."""
    code = str(code).strip()

    if not code:
        return ""

    # 42xxxxxxx → 51xxxxxxx
    if code.startswith("42"):
        code = "51" + code[2:]

    # 45xxxxxxx → 52xxxxxxx
    elif code.startswith("45"):
        code = "52" + code[2:]

    # 군위군
    elif code.startswith("47720"):
        code = "27720" + code[5:]

    return code


def normalize_sgg_code(code):
    """10자리 코드를 시군구 5자리 코드로 변환."""
    code = normalize_code(code)

    if len(code) >= 5:
        return code[:5]

    return code


def get_year_from_filename(filename):
    """
    파일명에서 연도를 찾는다.

    예:
    202606_202606_연령별인구현황_월간.csv
    → 2026
    """
    name = Path(filename).name

    # 202606
    matches = re.findall(r"(20\d{2})(?:0[1-9]|1[0-2])", name)

    if matches:
        return int(matches[0])

    # population_2026.csv
    matches = re.findall(r"(20\d{2})", name)

    if matches:
        return int(matches[0])

    return None


# =========================================================
# CSV 찾기
# =========================================================

@st.cache_data(show_spinner=False)
def find_population_files():
    """
    저장소의 여러 위치에서 CSV 검색.

    지원:
    ./population_2026.csv
    ./202606_202606_연령별인구현황_월간.csv
    ./data/...
    """

    candidates = []

    roots = [
        Path("."),
        Path("./data"),
    ]

    patterns = [
        "*.csv",
        "*.CSV",
    ]

    for root in roots:
        if not root.exists():
            continue

        for pattern in patterns:
            for path in root.glob(pattern):
                if path.is_file():
                    candidates.append(path)

    # 중복 제거
    unique = {}

    for path in candidates:
        unique[str(path.resolve())] = path

    candidates = list(unique.values())

    # 인구 관련 CSV만 우선적으로 사용
    population_candidates = []

    for path in candidates:
        name = path.name.lower()

        if (
            "인구" in path.name
            or "population" in name
            or "pop" in name
        ):
            population_candidates.append(path)

    if population_candidates:
        candidates = population_candidates

    # 연도별로 가장 적절한 파일 하나 선택
    year_files = {}

    for path in candidates:
        year = get_year_from_filename(path.name)

        if year is None:
            continue

        if year not in year_files:
            year_files[year] = path
        else:
            # 이름에 연령별인구현황이 들어간 파일을 우선
            old_name = year_files[year].name
            new_name = path.name

            if "연령별인구" in new_name and "연령별인구" not in old_name:
                year_files[year] = path

    return dict(sorted(year_files.items()))


# =========================================================
# CSV 읽기
# =========================================================

@st.cache_data(show_spinner=False)
def read_population_csv(path_string):
    """
    CP949 / EUC-KR / UTF-8-SIG / UTF-8 순서로 시도.
    """

    encodings = [
        "cp949",
        "euc-kr",
        "utf-8-sig",
        "utf-8",
    ]

    last_error = None

    for encoding in encodings:
        try:
            df = pd.read_csv(
                path_string,
                encoding=encoding,
                low_memory=False,
            )

            if len(df.columns) > 1:
                return df

        except Exception as e:
            last_error = e

    raise ValueError(
        f"CSV 파일을 읽을 수 없습니다.\n\n{last_error}"
    )


# =========================================================
# 연령 열 찾기
# =========================================================

def find_age_columns(df):
    """
    0세 ~ 100세 이상 전체 인구 열을 찾는다.

    예:
    2026년06월_계_0세
    2026년06월_계_1세
    ...
    2026년06월_계_100세 이상
    """

    age_columns = {}

    for column in df.columns:
        col = str(column)

        # 반드시 '계' 인구를 사용
        if "_계_" not in col:
            continue

        # 100세 이상
        if re.search(r"_100세\s*이상$", col):
            age_columns[100] = column
            continue

        # 0~99세
        match = re.search(r"_(\d{1,2})세$", col)

        if match:
            age = int(match.group(1))

            if 0 <= age <= 99:
                age_columns[age] = column

    return age_columns


# =========================================================
# 시군구 데이터 계산
# =========================================================

@st.cache_data(show_spinner=False)
def process_population_file(path_string):
    df = read_population_csv(path_string)

    # 행정구역 열 찾기
    area_column = None

    for candidate in ["행정구역", "행정구역명", "지역"]:
        if candidate in df.columns:
            area_column = candidate
            break

    if area_column is None:
        # 가장 앞쪽 문자열 열을 찾음
        for column in df.columns:
            if df[column].dtype == "object":
                area_column = column
                break

    if area_column is None:
        raise ValueError("행정구역 열을 찾지 못했습니다.")

    # 코드 추출
    df["_raw_code"] = df[area_column].apply(extract_code)
    df["_code"] = df["_raw_code"].apply(normalize_code)

    df["_sgg"] = df["_code"].apply(normalize_sgg_code)

    # 연령 열
    age_columns = find_age_columns(df)

    if not age_columns:
        raise ValueError(
            "연령별 인구 열을 찾지 못했습니다.\n"
            "예: 2026년06월_계_0세"
        )

    # 0~14세
    youth_columns = [
        column
        for age, column in age_columns.items()
        if 0 <= age <= 14
    ]

    # 65세 이상
    senior_columns = [
        column
        for age, column in age_columns.items()
        if age >= 65
    ]

    if not youth_columns:
        raise ValueError("0~14세 열을 찾지 못했습니다.")

    if not senior_columns:
        raise ValueError("65세 이상 열을 찾지 못했습니다.")

    # 숫자 변환
    for column in set(youth_columns + senior_columns):
        df[column] = df[column].apply(clean_number)

    df["_youth"] = df[youth_columns].sum(axis=1)
    df["_senior"] = df[senior_columns].sum(axis=1)

    # =====================================================
    # 가장 우선:
    # 시군구 자체 집계 행을 사용
    #
    # 예:
    # 1111000000
    # 1114000000
    #
    # 뒤 5자리가 00000이면 시군구 집계 행
    # =====================================================

    aggregate_mask = (
        df["_code"].str.len().eq(10)
        & df["_code"].str.endswith("00000")
    )

    aggregate = df[aggregate_mask].copy()

    # 시도 자체 행(1100000000 등)은 제외
    aggregate = aggregate[
        aggregate["_code"].str[2:5] != "000"
    ].copy()

    if len(aggregate) > 0:
        result = aggregate[
            ["_code", "_sgg", area_column, "_youth", "_senior"]
        ].copy()

        result.columns = [
            "code",
            "sgg_code",
            "name",
            "youth",
            "senior",
        ]

    else:
        # =================================================
        # 집계 행이 없는 CSV라면 읍면동 데이터를 합산
        # =================================================

        leaf = df[
            df["_code"].str.len().eq(10)
            & ~df["_code"].str.endswith("00000")
        ].copy()

        if len(leaf) == 0:
            raise ValueError("시군구 데이터를 만들 수 없습니다.")

        grouped = (
            leaf.groupby("_sgg", as_index=False)[
                ["_youth", "_senior"]
            ]
            .sum()
        )

        # 이름은 가장 먼저 발견된 행의 이름 사용
        names = (
            leaf.groupby("_sgg")[area_column]
            .first()
            .reset_index()
        )

        result = grouped.merge(
            names,
            on="_sgg",
            how="left",
        )

        result = result.rename(
            columns={
                "_sgg": "sgg_code",
                "_youth": "youth",
                "_senior": "senior",
                area_column: "name",
            }
        )

        result["code"] = result["sgg_code"] + "00000"

    # 숫자형
    result["youth"] = pd.to_numeric(
        result["youth"],
        errors="coerce",
    ).fillna(0)

    result["senior"] = pd.to_numeric(
        result["senior"],
        errors="coerce",
    ).fillna(0)

    # 총인구를 두 연령대만으로 계산하지 않고
    # 원본의 모든 연령 열을 사용
    total_columns = list(age_columns.values())

    if len(total_columns) > 0:
        for column in total_columns:
            df[column] = df[column].apply(clean_number)

        df["_total"] = df[total_columns].sum(axis=1)

        if len(aggregate) > 0:
            totals = aggregate[
                ["_code", "_total"]
            ].copy()

            totals["_code"] = totals["_code"].apply(normalize_code)

            totals = totals.rename(
                columns={
                    "_code": "code",
                    "_total": "total",
                }
            )

            result = result.merge(
                totals,
                on="code",
                how="left",
            )

        else:
            total_grouped = (
                leaf.groupby("_sgg")["_total"]
                .sum()
                .reset_index()
                .rename(
                    columns={
                        "_sgg": "sgg_code",
                        "_total": "total",
                    }
                )
            )

            result = result.merge(
                total_grouped,
                on="sgg_code",
                how="left",
            )

    # 총인구가 없는 경우 youth + senior로라도 계산
    result["total"] = pd.to_numeric(
        result["total"],
        errors="coerce",
    )

    result["total"] = result["total"].fillna(
        result["youth"] + result["senior"]
    )

    # 비율
    result["aging_rate"] = (
        result["senior"] / result["total"] * 100
    ).where(result["total"] > 0)

    result["youth_rate"] = (
        result["youth"] / result["total"] * 100
    ).where(result["total"] > 0)

    # 코드 정리
    result["sgg_code"] = (
        result["sgg_code"]
        .astype(str)
        .str.extract(r"(\d{5})")[0]
    )

    result["sido_code"] = result["sgg_code"].str[:2]

    result["name"] = (
        result["name"]
        .astype(str)
        .str.replace(r"\s*\(\d+\)\s*$", "", regex=True)
        .str.strip()
    )

    # 중복 시군구가 생겼다면 합산
    result = (
        result.groupby(
            ["sgg_code", "sido_code"],
            as_index=False
        )
        .agg(
            {
                "name": "first",
                "total": "sum",
                "youth": "sum",
                "senior": "sum",
            }
        )
    )

    result["aging_rate"] = (
        result["senior"] / result["total"] * 100
    ).where(result["total"] > 0)

    result["youth_rate"] = (
        result["youth"] / result["total"] * 100
    ).where(result["total"] > 0)

    return result


# =========================================================
# GeoJSON
# =========================================================

@st.cache_data(show_spinner=False)
def load_geojson():
    request = urllib.request.Request(
        GEOJSON_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:
        data = response.read()

    return json.loads(
        data.decode("utf-8")
    )


def get_feature_code(feature):
    properties = feature.get("properties", {})

    for key in [
        "adm_cd2",
        "adm_cd",
        "code",
        "CODE",
    ]:
        value = properties.get(key)

        if value is not None:
            value = str(value).strip()

            # 숫자로 변환되면서 .0이 붙는 경우
            if value.endswith(".0"):
                value = value[:-2]

            if value:
                return normalize_code(value)

    return ""


def get_feature_name(feature):
    properties = feature.get("properties", {})

    for key in [
        "adm_nm",
        "ADM_NM",
        "name",
        "NAME",
    ]:
        value = properties.get(key)

        if value:
            return str(value).strip()

    return ""


def polygon_list_from_geometry(geometry):
    """
    GeoJSON Geometry → MultiPolygon 형태의 리스트

    Polygon:
      coordinates = [ring, ring, ...]

    MultiPolygon:
      coordinates = [[ring, ring, ...], ...]
    """

    if not geometry:
        return []

    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])

    if geom_type == "Polygon":
        return [coordinates]

    if geom_type == "MultiPolygon":
        return coordinates

    return []


@st.cache_data(show_spinner=False)
def make_sgg_geometries(geojson):
    """
    행정동 GeoJSON을 5자리 시군구 코드별로 합친다.
    """

    grouped = {}

    for feature in geojson.get("features", []):
        code = get_feature_code(feature)

        if len(code) != 10:
            continue

        sgg_code = code[:5]

        # 시도 자체 코드 제외
        if code[2:] == "00000000":
            continue

        geometry = feature.get("geometry")

        polygons = polygon_list_from_geometry(
            geometry
        )

        if not polygons:
            continue

        if sgg_code not in grouped:
            grouped[sgg_code] = {
                "sgg_code": sgg_code,
                "name": get_feature_name(feature),
                "polygons": [],
                "sido_code": sgg_code[:2],
            }

        grouped[sgg_code]["polygons"].extend(
            polygons
        )

        # 더 긴 이름을 얻을 수 있으면 사용
        current_name = grouped[sgg_code]["name"]
        new_name = get_feature_name(feature)

        if new_name:
            if len(new_name) < len(current_name) or not current_name:
                grouped[sgg_code]["name"] = new_name

    return grouped


# =========================================================
# 시도 이름
# =========================================================

def infer_sido_name_from_sgg_name(name):
    """
    행정동 이름에서 시도명을 추정.
    """

    if not name:
        return ""

    first = str(name).split()[0]

    # 특별자치시/특별시/광역시 등은 그대로
    if first.endswith(
        (
            "특별시",
            "광역시",
            "특별자치시",
            "특별자치도",
            "도",
        )
    ):
        return first

    return ""


@st.cache_data(show_spinner=False)
def make_sido_names(geojson, sgg_geometries):
    names = {}

    # GeoJSON 전체에서 직접 찾기
    for feature in geojson.get("features", []):
        code = get_feature_code(feature)

        if len(code) != 10:
            continue

        sido_code = code[:2]
        name = get_feature_name(feature)

        if not name:
            continue

        inferred = infer_sido_name_from_sgg_name(name)

        if inferred:
            names[sido_code] = inferred

    # 시군구 이름에서도 추정
    for sgg_code, item in sgg_geometries.items():
        sido_code = sgg_code[:2]

        name = item.get("name", "")

        inferred = infer_sido_name_from_sgg_name(name)

        if inferred and sido_code not in names:
            names[sido_code] = inferred

    # fallback
    for code, name in SIDO_FALLBACK.items():
        if code not in names:
            names[code] = name

    return names


# =========================================================
# 지도 색상
# =========================================================

def value_to_color(value, metric):
    """
    값 → 지도 색상.

    고령화:
    <19
    19~23
    23~28
    28~38
    >=38

    유소년:
    <5
    5~7
    7~9
    9~11
    >=11
    """

    if value is None or pd.isna(value):
        return "#d1d5db"

    value = float(value)

    if metric == "aging":
        breaks = AGING_BREAKS
        colors = [
            "#fff7bc",
            "#fec44f",
            "#fe9929",
            "#ec7014",
            "#cc4c02",
        ]
    else:
        breaks = YOUTH_BREAKS
        colors = [
            "#eff6ff",
            "#bfdbfe",
            "#60a5fa",
            "#2563eb",
            "#1d4ed8",
        ]

    if value < breaks[0]:
        return colors[0]

    if value < breaks[1]:
        return colors[1]

    if value < breaks[2]:
        return colors[2]

    if value < breaks[3]:
        return colors[3]

    return colors[4]


def format_range_labels(metric):
    if metric == "aging":
        return [
            "< 19%",
            "19–23%",
            "23–28%",
            "28–38%",
            "≥ 38%",
        ]

    return [
        "< 5%",
        "5–7%",
        "7–9%",
        "9–11%",
        "≥ 11%",
    ]


# =========================================================
# SVG 지도
# =========================================================

def flatten_coordinates(polygons):
    """
    MultiPolygon 리스트에서 모든 좌표를 평탄화.
    """

    points = []

    for polygon in polygons:
        for ring in polygon:
            for point in ring:
                if len(point) >= 2:
                    points.append(
                        (float(point[0]), float(point[1]))
                    )

    return points


def get_bbox(items):
    points = []

    for item in items:
        points.extend(
            flatten_coordinates(
                item["polygons"]
            )
        )

    if not points:
        return None

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    return (
        min(xs),
        min(ys),
        max(xs),
        max(ys),
    )


def project_point(
    lon,
    lat,
    bbox,
    width,
    height,
    padding=20,
):
    min_lon, min_lat, max_lon, max_lat = bbox

    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    if lon_range == 0:
        lon_range = 1

    if lat_range == 0:
        lat_range = 1

    usable_width = width - padding * 2
    usable_height = height - padding * 2

    # 종횡비 유지
    scale = min(
        usable_width / lon_range,
        usable_height / lat_range,
    )

    map_width = lon_range * scale
    map_height = lat_range * scale

    offset_x = (
        padding
        + (usable_width - map_width) / 2
    )

    offset_y = (
        padding
        + (usable_height - map_height) / 2
    )

    x = (
        offset_x
        + (lon - min_lon) * scale
    )

    # SVG y축은 아래로 증가
    y = (
        offset_y
        + (max_lat - lat) * scale
    )

    return x, y


def make_path(
    polygons,
    bbox,
    width,
    height,
):
    paths = []

    for polygon in polygons:
        for ring in polygon:
            if not ring:
                continue

            commands = []

            for i, point in enumerate(ring):
                if len(point) < 2:
                    continue

                x, y = project_point(
                    point[0],
                    point[1],
                    bbox,
                    width,
                    height,
                )

                if i == 0:
                    commands.append(
                        f"M {x:.2f} {y:.2f}"
                    )
                else:
                    commands.append(
                        f"L {x:.2f} {y:.2f}"
                    )

            commands.append("Z")

            paths.append(
                " ".join(commands)
            )

    return " ".join(paths)


def escape_html(text):
    text = str(text)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def make_map_svg(
    map_items,
    metric,
    selected_sido,
    sido_names,
):
    width = 1000
    height = 680

    if selected_sido == "전국":
        visible_items = list(
            map_items.values()
        )
    else:
        visible_items = [
            item
            for item in map_items.values()
            if item["sido_code"] == selected_sido
        ]

    if not visible_items:
        return """
        <div style="
            padding:50px;
            text-align:center;
            color:#777;
        ">
            표시할 행정구역이 없습니다.
        </div>
        """

    bbox = get_bbox(
        visible_items
    )

    if bbox is None:
        return """
        <div style="
            padding:50px;
            text-align:center;
        ">
            지도 좌표를 읽을 수 없습니다.
        </div>
        """

    # 데이터와 지도 결합
    svg_parts = []

    svg_parts.append(
        f"""
        <svg
            viewBox="0 0 {width} {height}"
            width="100%"
            height="680"
            xmlns="http://www.w3.org/2000/svg"
            style="
                background:#fafafa;
                border-radius:16px;
                border:1px solid #e5e7eb;
            "
        >
        """
    )

    # 제목
    if selected_sido == "전국":
        title = "대한민국 시군구"
    else:
        title = sido_names.get(
            selected_sido,
            selected_sido,
        )

    svg_parts.append(
        f"""
        <text
            x="25"
            y="38"
            font-size="22"
            font-weight="700"
            fill="#222"
        >
            {escape_html(title)}
        </text>
        """
    )

    for item in visible_items:
        value = item.get("value")

        if value is None or pd.isna(value):
            fill = "#d1d5db"
        else:
            fill = value_to_color(
                value,
                metric,
            )

        path = make_path(
            item["polygons"],
            bbox,
            width,
            height,
        )

        name = item.get(
            "name",
            item["sgg_code"],
        )

        if value is None or pd.isna(value):
            tooltip = (
                f"{name}&#10;"
                "인구 데이터 없음"
            )
        else:
            if metric == "aging":
                metric_name = "고령화율"
            else:
                metric_name = "유소년율"

            tooltip = (
                f"{name}&#10;"
                f"{metric_name}: {value:.1f}%"
            )

        svg_parts.append(
            f"""
            <path
                d="{path}"
                fill="{fill}"
                stroke="#ffffff"
                stroke-width="0.8"
                stroke-linejoin="round"
            >
                <title>{escape_html(tooltip)}</title>
            </path>
            """
        )

    svg_parts.append("</svg>")

    return "".join(svg_parts)


# =========================================================
# KPI
# =========================================================

def make_kpi_card(
    title,
    value,
    sub="",
):
    return f"""
    <div class="kpi-card">
        <div class="kpi-title">
            {escape_html(title)}
        </div>
        <div class="kpi-value">
            {escape_html(value)}
        </div>
        <div class="kpi-sub">
            {escape_html(sub)}
        </div>
    </div>
    """


# =========================================================
# 앱 시작
# =========================================================

st.markdown(
    '<div class="main-title">🗺️ 대한민국 인구구조 지도</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    "연도와 지표를 선택하여 전국 시군구의 인구구조를 비교해 보세요."
    "</div>",
    unsafe_allow_html=True,
)


# =========================================================
# CSV 확인
# =========================================================

population_files = find_population_files()

if not population_files:
    st.error(
        "연도별 인구 CSV 파일을 찾지 못했습니다."
    )

    st.markdown(
        """
        ### 📁 CSV 파일 넣는 방법

        GitHub 저장소를 다음처럼 만들어 주세요.

        ```
        main.py
        data/
        ├─ 202206_202206_연령별인구현황_월간.csv
        ├─ 202306_202306_연령별인구현황_월간.csv
        ├─ 202406_202406_연령별인구현황_월간.csv
        ├─ 202506_202506_연령별인구현황_월간.csv
        └─ 202606_202606_연령별인구현황_월간.csv
        ```

        `data` 폴더가 없어도 됩니다. CSV를 `main.py`와 같은 위치에 넣어도 자동으로 찾습니다.

        **중요:** 실제 CSV가 여러 연도 있어야 연도 슬라이더에서도 여러 연도가 나타납니다.
        """
    )

    st.stop()


available_years = list(
    population_files.keys()
)


# =========================================================
# 경계 데이터
# =========================================================

try:
    with st.spinner("행정구역 경계 데이터를 불러오는 중..."):
        geojson = load_geojson()

        sgg_geometries = make_sgg_geometries(
            geojson
        )

        sido_names = make_sido_names(
            geojson,
            sgg_geometries,
        )

except Exception as e:
    st.error(
        "행정구역 경계 데이터를 불러오지 못했습니다."
    )

    st.code(str(e))

    st.stop()


# =========================================================
# 사이드바
# =========================================================

with st.sidebar:

    st.header("⚙️ 지도 설정")

    # 실제 존재하는 연도만 슬라이더
    if len(available_years) == 1:
        selected_year = available_years[0]

        st.info(
            f"현재 CSV가 {selected_year}년 하나뿐입니다."
        )

    else:
        selected_year = st.select_slider(
            "연도",
            options=available_years,
            value=available_years[-1],
            format_func=lambda x: f"{x}년",
        )

    metric_label = st.selectbox(
        "지표",
        [
            "65세 이상 고령화율",
            "0~14세 유소년율",
        ],
    )

    if metric_label == "65세 이상 고령화율":
        metric = "aging"
    else:
        metric = "youth"

    # 시도 선택
    sido_options = {
        "전국": "전국"
    }

    for code, name in sorted(
        sido_names.items(),
        key=lambda x: x[1],
    ):
        sido_options[name] = code

    selected_sido_name = st.selectbox(
        "시도",
        list(sido_options.keys()),
    )

    selected_sido = sido_options[
        selected_sido_name
    ]

    st.divider()

    st.markdown("### 🎨 색 구간")

    if metric == "aging":
        st.write(
            "19% · 23% · 28% · 38%"
        )
    else:
        st.write(
            "5% · 7% · 9% · 11%"
        )

    st.caption(
        "회색 지역은 현재 선택한 연도의 인구 데이터와 "
        "행정구역 경계가 일치하지 않는 지역입니다."
    )


# =========================================================
# 선택 연도 CSV 읽기
# =========================================================

selected_file = population_files[
    selected_year
]

try:
    with st.spinne:
