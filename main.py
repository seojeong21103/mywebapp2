import re
import json
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# =========================================================
# 페이지 설정
# =========================================================

st.set_page_config(
    page_title="전국 고령화 지도",
    page_icon="🗺️",
    layout="wide",
)


# =========================================================
# 기본 설정
# =========================================================

GEOJSON_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    "ver20260701/HangJeongDong_ver20260701.geojson"
)

# 고령화율 구간
# 연도가 바뀌어도 이 값은 절대 바뀌지 않음
AGING_BREAKS = [19, 23, 28, 38]

# 유소년율 구간
YOUTH_BREAKS = [5, 7, 9, 11]

# 행정구역 개편 코드
CODE_REPLACEMENTS = {
    "42": "51",        # 옛 강원 → 강원특별자치도
    "45": "52",        # 옛 전북 → 전북특별자치도
    "47720": "27720",  # 군위군
}

# 시도명 예비값
SIDO_NAMES_FALLBACK = {
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
# CSS
# =========================================================

st.markdown(
    """
    <style>

    .title {
        font-size: 2.2rem;
        font-weight: 800;
        margin-bottom: 3px;
    }

    .subtitle {
        color: #777;
        margin-bottom: 20px;
    }

    .card {
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 16px;
        padding: 18px;
        min-height: 125px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }

    .card-title {
        color: #777;
        font-size: 0.9rem;
        margin-bottom: 8px;
    }

    .card-value {
        font-size: 1.45rem;
        font-weight: 800;
        color: #222;
    }

    .card-sub {
        color: #888;
        font-size: 0.8rem;
        margin-top: 6px;
    }

    .notice {
        background: #f7f7f7;
        border-radius: 12px;
        padding: 13px 16px;
        color: #555;
        margin-top: 10px;
        margin-bottom: 15px;
        font-size: 0.9rem;
    }

    .source {
        color: #888;
        font-size: 0.75rem;
        margin-top: 25px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 숫자 변환
# =========================================================

def to_number(value):
    if pd.isna(value):
        return 0.0

    text = str(value).strip()
    text = text.replace(",", "")
    text = text.replace(" ", "")

    if text in ("", "-", "nan", "None"):
        return 0.0

    try:
        return float(text)
    except Exception:
        return 0.0


# =========================================================
# 행정구역 코드 추출
# =========================================================

def extract_code(value):
    """
    예:
    서울특별시 종로구 (1111000000)
    → 1111000000
    """

    if pd.isna(value):
        return ""

    text = str(value)

    matches = re.findall(r"\d{10}", text)

    if matches:
        return matches[-1]

    return ""


# =========================================================
# 행정구역 코드 변경
# =========================================================

def normalize_code(code):
    code = str(code).strip()

    if not code:
        return ""

    # 강원
    if code.startswith("42"):
        code = "51" + code[2:]

    # 전북
    elif code.startswith("45"):
        code = "52" + code[2:]

    # 군위군
    elif code.startswith("47720"):
        code = "27720" + code[5:]

    return code


def get_sgg_code(code):
    code = normalize_code(code)

    if len(code) >= 5:
        return code[:5]

    return ""


# =========================================================
# 연도 추출
# =========================================================

def extract_year(filename):
    """
    202606_202606_연령별인구현황_월간.csv
    → 2026
    """

    name = Path(filename).name

    # YYYYMM
    match = re.search(
        r"(20\d{2})(0[1-9]|1[0-2])",
        name
    )

    if match:
        return int(match.group(1))

    # YYYY
    match = re.search(
        r"(20\d{2})",
        name
    )

    if match:
        return int(match.group(1))

    return None


# =========================================================
# CSV 파일 찾기
# =========================================================

@st.cache_data(show_spinner=False)
def find_population_files():

    roots = [
        Path("."),
        Path("./data"),
    ]

    files = []

    for root in roots:

        if not root.exists():
            continue

        for path in root.glob("*.csv"):

            if path.is_file():
                files.append(path)

    # 인구 관련 CSV 우선
    population_files = []

    for path in files:

        name = path.name.lower()

        if (
            "인구" in path.name
            or "population" in name
            or "pop" in name
        ):
            population_files.append(path)

    if population_files:
        files = population_files

    result = {}

    for path in files:

        year = extract_year(path.name)

        if year is None:
            continue

        # 같은 연도 파일이 여러 개라면
        # 연령별인구현황 파일을 우선
        if year not in result:

            result[year] = path

        else:

            old_name = result[year].name
            new_name = path.name

            if (
                "연령별인구" in new_name
                and "연령별인구" not in old_name
            ):
                result[year] = path

    return dict(
        sorted(result.items())
    )


# =========================================================
# CSV 읽기
# =========================================================

@st.cache_data(show_spinner=False)
def read_csv_file(path):

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
                path,
                encoding=encoding,
                low_memory=False,
            )

            if len(df.columns) >= 2:
                return df

        except Exception as e:
            last_error = e

    raise ValueError(
        f"CSV 파일을 읽을 수 없습니다.\n{last_error}"
    )


# =========================================================
# 연령별 열 찾기
# =========================================================

def find_age_columns(df):

    age_columns = {}

    for column in df.columns:

        text = str(column)

        if "_계_" not in text:
            continue

        # 100세 이상
        if re.search(
            r"_100세\s*이상$",
            text
        ):
            age_columns[100] = column
            continue

        # 0~99세
        match = re.search(
            r"_(\d{1,2})세$",
            text
        )

        if match:

            age = int(
                match.group(1)
            )

            if 0 <= age <= 99:
                age_columns[age] = column

    return age_columns


# =========================================================
# 인구 데이터 처리
# =========================================================

@st.cache_data(show_spinner=False)
def process_population(path):

    df = read_csv_file(path)

    # ---------------------------------------------
    # 행정구역 열 찾기
    # ---------------------------------------------

    area_column = None

    for candidate in [
        "행정구역",
        "행정구역명",
        "지역",
    ]:

        if candidate in df.columns:
            area_column = candidate
            break

    if area_column is None:

        for column in df.columns:

            if df[column].dtype == "object":
                area_column = column
                break

    if area_column is None:
        raise ValueError(
            "행정구역 열을 찾을 수 없습니다."
        )

    # ---------------------------------------------
    # 코드
    # ---------------------------------------------

    df["_code"] = (
        df[area_column]
        .apply(extract_code)
        .apply(normalize_code)
    )

    df["_sgg"] = (
        df["_code"]
        .apply(get_sgg_code)
    )

    # ---------------------------------------------
    # 연령 열
    # ---------------------------------------------

    age_columns = find_age_columns(df)

    if not age_columns:

        raise ValueError(
            "연령별 인구 열을 찾을 수 없습니다."
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

    # 모든 연령
    total_columns = list(
        age_columns.values()
    )

    # ---------------------------------------------
    # 숫자 변환
    # ---------------------------------------------

    for column in total_columns:

        df[column] = (
            df[column]
            .apply(to_number)
        )

    df["_youth"] = (
        df[youth_columns]
        .sum(axis=1)
    )

    df["_senior"] = (
        df[senior_columns]
        .sum(axis=1)
    )

    df["_total"] = (
        df[total_columns]
        .sum(axis=1)
    )

    # ---------------------------------------------
    # 시군구 자체 집계 행 찾기
    #
    # 예:
    # 1111000000
    #
    # 마지막 5자리가 00000
    # ---------------------------------------------

    aggregate_mask = (
        df["_code"]
        .astype(str)
        .str.len()
        .eq(10)
        &
        df["_code"]
        .astype(str)
        .str.endswith("00000")
    )

    aggregate = df[
        aggregate_mask
    ].copy()

    # 시도 전체 행 제외
    aggregate = aggregate[
        ~aggregate["_code"]
        .astype(str)
        .str.endswith("00000000")
    ].copy()

    # ---------------------------------------------
    # 집계 행이 있는 경우
    # ---------------------------------------------

    if len(aggregate) > 0:

        result = aggregate[
            [
                "_code",
                "_sgg",
                area_column,
                "_total",
                "_youth",
                "_senior",
            ]
        ].copy()

        result.columns = [
            "code",
            "sgg_code",
            "name",
            "total",
            "youth",
            "senior",
        ]

    # ---------------------------------------------
    # 집계 행이 없는 경우
    # 읍면동을 시군구별로 합산
    # ---------------------------------------------

    else:

        leaf = df[
            df["_code"]
            .astype(str)
            .str.len()
            .eq(10)
        ].copy()

        leaf = leaf[
            ~leaf["_code"]
            .astype(str)
            .str.endswith("00000")
        ].copy()

        if len(leaf) == 0:
            raise ValueError(
                "시군구 데이터를 만들 수 없습니다."
            )

        grouped = (
            leaf
            .groupby("_sgg", as_index=False)
            [
                [
                    "_total",
                    "_youth",
                    "_senior",
                ]
            ]
            .sum()
        )

        names = (
            leaf
            .groupby("_sgg")[area_column]
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
                "_total": "total",
                "_youth": "youth",
                "_senior": "senior",
                area_column: "name",
            }
        )

        result["code"] = (
            result["sgg_code"]
            + "00000"
        )

    # ---------------------------------------------
    # 숫자형
    # ---------------------------------------------

    for column in [
        "total",
        "youth",
        "senior",
    ]:

        result[column] = (
            pd.to_numeric(
                result[column],
                errors="coerce",
            )
            .fillna(0)
        )

    # ---------------------------------------------
    # 비율
    # ---------------------------------------------

    result["aging_rate"] = (
        result["senior"]
        / result["total"]
        * 100
    ).where(
        result["total"] > 0
    )

    result["youth_rate"] = (
        result["youth"]
        / result["total"]
        * 100
    ).where(
        result["total"] > 0
    )

    # ---------------------------------------------
    # 코드 정리
    # ---------------------------------------------

    result["sgg_code"] = (
        result["sgg_code"]
        .astype(str)
        .str.extract(
            r"(\d{5})"
        )[0]
    )

    result["sido_code"] = (
        result["sgg_code"]
        .str[:2]
    )

    # 이름에서 코드 제거
    result["name"] = (
        result["name"]
        .astype(str)
        .str.replace(
            r"\s*\(\d+\)\s*$",
            "",
            regex=True,
        )
        .str.strip()
    )

    # ---------------------------------------------
    # 중복 시군구 제거
    # ---------------------------------------------

    result = (
        result
        .groupby(
            [
                "sgg_code",
                "sido_code",
            ],
            as_index=False,
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

    # 비율 다시 계산
    result["aging_rate"] = (
        result["senior"]
        / result["total"]
        * 100
    ).where(
        result["total"] > 0
    )

    result["youth_rate"] = (
        result["youth"]
        / result["total"]
        * 100
    ).where(
        result["total"] > 0
    )

    return result


# =========================================================
# GeoJSON 다운로드
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


# =========================================================
# GeoJSON 코드
# =========================================================

def get_geo_code(feature):

    properties = feature.get(
        "properties",
        {}
    )

    for key in [
        "adm_cd2",
        "adm_cd",
        "code",
        "CODE",
    ]:

        value = properties.get(key)

        if value is None:
            continue

        value = str(value).strip()

        if value.endswith(".0"):
            value = value[:-2]

        if value:
            return normalize_code(value)

    return ""


# =========================================================
# GeoJSON 이름
# =========================================================

def get_geo_name(feature):

    properties = feature.get(
        "properties",
        {}
    )

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


# =========================================================
# Geometry → Polygon 목록
# =========================================================

def geometry_to_polygons(geometry):

    if not geometry:
        return []

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates",
        []
    )

    # Polygon
    if geometry_type == "Polygon":

        return [
            coordinates
        ]

    # MultiPolygon
    if geometry_type == "MultiPolygon":

        return coordinates

    return []


# =========================================================
# 시군구 경계 만들기
# =========================================================

@st.cache_data(show_spinner=False)
def build_sgg_geometries(geojson):

    result = {}

    for feature in geojson.get(
        "features",
        []
    ):

        code = get_geo_code(
            feature
        )

        if len(code) != 10:
            continue

        sgg_code = code[:5]

        # 시도 자체 제외
        if code[2:] == "00000000":
            continue

        geometry = feature.get(
            "geometry"
        )

        polygons = geometry_to_polygons(
            geometry
        )

        if not polygons:
            continue

        if sgg_code not in result:

            result[sgg_code] = {
                "sgg_code": sgg_code,
                "sido_code": sgg_code[:2],
                "name": get_geo_name(
                    feature
                ),
                "polygons": [],
            }

        result[sgg_code]["polygons"].extend(
            polygons
        )

    return result


# =========================================================
# 시도 이름 만들기
# =========================================================

def guess_sido_name(name):

    if not name:
        return ""

    first = (
        str(name)
        .strip()
        .split()[0]
    )

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
def build_sido_names(
    geojson,
    sgg_geometries,
):

    result = {}

    # GeoJSON에서 추출
    for feature in geojson.get(
        "features",
        []
    ):

        code = get_geo_code(
            feature
        )

        if len(code) != 10:
            continue

        sido_code = code[:2]

        name = get_geo_name(
            feature
        )

        guessed = guess_sido_name(
            name
        )

        if guessed:
            result[sido_code] = guessed

    # 시군구에서도 추출
    for sgg_code, item in sgg_geometries.items():

        sido_code = sgg_code[:2]

        guessed = guess_sido_name(
            item.get("name", "")
        )

        if guessed and sido_code not in result:
            result[sido_code] = guessed

    # fallback
    for code, name in SIDO_NAMES_FALLBACK.items():

        if code not in result:
            result[code] = name

    return result


# =========================================================
# 지도 색상
# =========================================================

def get_color(value, metric):

    if value is None or pd.isna(value):
        return "#d1d5db"

    value = float(value)

    if metric == "aging":

        # 19 / 23 / 28 / 38 고정
        if value < 19:
            return "#fff7bc"

        elif value < 23:
            return "#fec44f"

        elif value < 28:
            return "#fe9929"

        elif value < 38:
            return "#ec7014"

        else:
            return "#cc4c02"

    else:

        # 5 / 7 / 9 / 11 고정
        if value < 5:
            return "#eff6ff"

        elif value < 7:
            return "#bfdbfe"

        elif value < 9:
            return "#60a5fa"

        elif value < 11:
            return "#2563eb"

        else:
            return "#1d4ed8"


# =========================================================
# 지도 범례
# =========================================================

def get_legend(metric):

    if metric == "aging":

        return [
            ("< 19%", 10),
            ("19–23%", 21),
            ("23–28%", 25),
            ("28–38%", 33),
            ("≥ 38%", 40),
        ]

    return [
        ("< 5%", 3),
        ("5–7%", 6),
        ("7–9%", 8),
        ("9–11%", 10),
        ("≥ 11%", 12),
    ]


# =========================================================
# 좌표 처리
# =========================================================

def all_points(polygons):

    points = []

    for polygon in polygons:

        for ring in polygon:

            for point in ring:

                if len(point) >= 2:

                    points.append(
                        (
                            float(point[0]),
                            float(point[1]),
                        )
                    )

    return points


def get_bbox(items):

    points = []

    for item in items:

        points.extend(
            all_points(
                item["polygons"]
            )
        )

    if not points:
        return None

    xs = [
        point[0]
        for point in points
    ]

    ys = [
        point[1]
        for point in points
    ]

    return (
        min(xs),
        min(ys),
        max(xs),
        max(ys),
    )


def project(
    lon,
    lat,
    bbox,
    width,
    height,
):

    min_lon, min_lat, max_lon, max_lat = bbox

    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    if lon_range == 0:
        lon_range = 1

    if lat_range == 0:
        lat_range = 1

    padding = 25

    usable_width = (
        width - padding * 2
    )

    usable_height = (
        height - padding * 2
    )

    scale = min(
        usable_width / lon_range,
        usable_height / lat_range,
    )

    map_width = (
        lon_range * scale
    )

    map_height = (
        lat_range * scale
    )

    offset_x = (
        padding
        + (
            usable_width
            - map_width
        ) / 2
    )

    offset_y = (
        padding
        + (
            usable_height
            - map_height
        ) / 2
    )

    x = (
        offset_x
        + (
            lon - min_lon
        ) * scale
    )

    y = (
        offset_y
        + (
            max_lat - lat
        ) * scale
    )

    return x, y


# =========================================================
# SVG Path
# =========================================================

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

                x, y = project(
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


# =========================================================
# HTML 특수문자 처리
# =========================================================

def escape_html(text):

    text = str(text)

    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# =========================================================
# SVG 지도 생성
# =========================================================

def create_map(
    map_items,
    metric,
    selected_sido,
    sido_names,
):

    width = 1000
    height = 680

    # 전국
    if selected_sido == "전국":

        visible_items = list(
            map_items.values()
        )

    # 특정 시도
    else:

        visible_items = [
            item
            for item in map_items.values()
            if item["sido_code"]
            == selected_sido
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

    if selected_sido == "전국":

        title = "대한민국 시군구"

    else:

        title = sido_names.get(
            selected_sido,
            selected_sido,
        )

    html = f"""
    <svg
        viewBox="0 0 {width} {height}"
        width="100%"
        height="680"
        xmlns="http://www.w3.org/2000/svg"
        style="
            background:#fafafa;
            border:1px solid #e5e7eb;
            border-radius:16px;
        "
    >

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

    # ---------------------------------------------
    # 지역 그리기
    # ---------------------------------------------

    for item in visible_items:

        value = item.get(
            "value"
        )

        fill = get_color(
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
                f"{name}\n"
                "인구 데이터 없음"
            )

        else:

            if metric == "aging":
                metric_name = "고령화율"
            else:
                metric_name = "유소년율"

            tooltip = (
                f"{name}\n"
                f"{metric_name}: "
                f"{float(value):.1f}%"
            )

        html += f"""
        <path
            d="{path}"
            fill="{fill}"
            stroke="#ffffff"
            stroke-width="0.8"
            stroke-linejoin="round"
        >
            <title>
                {escape_html(tooltip)}
            </title>
        </path>
        """

    html += "</svg>"

    return html


# =========================================================
# KPI 카드
# =========================================================

def kpi_card(
    title,
    value,
    sub,
):

    return f"""
    <div class="card">

        <div class="card-title">
            {escape_html(title)}
        </div>

        <div class="card-value">
            {escape_html(value)}
        </div>

        <div class="card-sub">
            {escape_html(sub)}
        </div>

    </div>
    """


# =========================================================
# 제목
# =========================================================

st.markdown(
    '<div class="title">🗺️ 전국 고령화 지도</div>',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="subtitle">
    시군구별 인구구조를 연도와 지표별로 비교해 볼 수 있습니다.
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# CSV 확인
# =========================================================

population_files = (
    find_population_files()
)

if not population_files:

    st.error(
        "연도별 인구 CSV 파일을 찾을 수 없습니다."
    )

    st.markdown(
        """
        ### 📁 CSV 파일을 넣어 주세요

        GitHub 저장소를 다음처럼 만들어 주세요.

        ```text
        main.py
        data/
        ├─ 202206_202206_연령별인구현황_월간.csv
        ├─ 202306_202306_연령별인구현황_월간.csv
        ├─ 202406_202406_연령별인구현황_월간.csv
        ├─ 202506_202506_연령별인구현황_월간.csv
        └─ 202606_202606_연령별인구현황_월간.csv
        ```

        또는 CSV 파일을 `main.py`와 같은 폴더에 넣어도 됩니다.
        """
    )

    st.stop()


available_years = list(
    population_files.keys()
)


# =========================================================
# 행정구역 경계
# =========================================================

try:

    with st.spinner(
        "행정구역 경계를 불러오는 중..."
    ):

        geojson = load_geojson()

        sgg_geometries = (
            build_sgg_geometries(
                geojson
            )
        )

        sido_names = (
            build_sido_names(
                geojson,
                sgg_geometries,
            )
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

    # ---------------------------------------------
    # 연도
    # ---------------------------------------------

    if len(available_years) == 1:

        selected_year = (
            available_years[0]
        )

        st.info(
            f"{selected_year}년 CSV만 있습니다."
        )

    else:

        selected_year = st.select_slider(
            "📅 연도",
            options=available_years,
            value=available_years[-1],
            format_func=lambda year:
                f"{year}년",
        )

    # ---------------------------------------------
    # 지표
    # ---------------------------------------------

    metric_label = st.selectbox(
