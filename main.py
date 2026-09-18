import streamlit as st
import pandas as pd
import json
import urllib.request
import re
import os
import glob
from collections import defaultdict


# ============================================================
# 기본 설정
# ============================================================

st.set_page_config(
    page_title="전국 고령화 지도",
    page_icon="🗺️",
    layout="wide"
)


# ============================================================
# CSS
# ============================================================

st.markdown("""
<style>

.main-title {
    font-size: 38px;
    font-weight: 800;
    margin-bottom: 5px;
}

.sub-title {
    color: #6b7280;
    font-size: 16px;
    margin-bottom: 25px;
}

.metric-card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    padding: 18px 20px;
    min-height: 120px;
}

.metric-title {
    color: #6b7280;
    font-size: 14px;
    margin-bottom: 8px;
}

.metric-value {
    font-size: 25px;
    font-weight: 800;
}

.metric-name {
    color: #374151;
    font-size: 14px;
    margin-top: 5px;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# 제목
# ============================================================

st.markdown(
    '<div class="main-title">🗺️ 전국 고령화 지도</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="sub-title">'
    '시군구별 인구 구조를 연도별로 비교하는 단계구분도'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# 설정
# ============================================================

GEOJSON_URL = (
    "https://raw.githubusercontent.com/"
    "vuski/admdongkor/master/"
    "ver20260701/HangJeongDong_ver20260701.geojson"
)


# ============================================================
# CSV 파일 찾기
# ============================================================

def find_population_files():

    files = []

    patterns = [
        "population_*.csv",
        "*연령별인구현황*.csv",
        "*연령별인구*.csv"
    ]

    for pattern in patterns:

        files.extend(
            glob.glob(pattern)
        )

    files = list(
        dict.fromkeys(files)
    )

    result = {}

    for file in files:

        filename = os.path.basename(file)

        years = re.findall(
            r"(20\d{2})",
            filename
        )

        if years:

            year = int(years[0])
            result[year] = file

    return dict(
        sorted(result.items())
    )


population_files = find_population_files()


if not population_files:

    st.error(
        "연도별 인구 CSV 파일을 찾을 수 없습니다."
    )

    st.info(
        "GitHub 저장소의 main.py와 같은 폴더에 "
        "`population_2022.csv`, `population_2023.csv` "
        "등의 파일을 넣어주세요."
    )

    st.stop()


# ============================================================
# 행정구역 경계 불러오기
# ============================================================

@st.cache_data(ttl=86400)
def load_geojson():

    with urllib.request.urlopen(
        GEOJSON_URL,
        timeout=120
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# CSV 읽기
# ============================================================

@st.cache_data
def read_population_csv(path):

    encodings = [
        "cp949",
        "euc-kr",
        "utf-8-sig",
        "utf-8"
    ]

    last_error = None

    for encoding in encodings:

        try:

            return pd.read_csv(
                path,
                encoding=encoding
            )

        except Exception as e:

            last_error = e

    raise last_error


# ============================================================
# 숫자 변환
# ============================================================

def number(value):

    if pd.isna(value):
        return 0.0

    text = str(value)

    text = (
        text
        .replace(",", "")
        .replace('"', "")
        .strip()
    )

    try:
        return float(text)

    except Exception:
        return 0.0


# ============================================================
# 행정구역 코드 추출
# ============================================================

def extract_code(text):

    text = str(text)

    # 괄호 안 숫자
    matches = re.findall(
        r"\((\d+)\)",
        text
    )

    if matches:
        return matches[-1]

    # 문자열 끝 숫자
    match = re.search(
        r"(\d{2,10})$",
        text
    )

    if match:
        return match.group(1)

    return ""


# ============================================================
# 행정구역 코드 보정
# ============================================================

def normalize_code(code):

    code = str(code)

    # --------------------------------------------------------
    # 옛 시도 코드 변경
    #
    # 42 → 51 : 강원
    # 45 → 52 : 전북
    # --------------------------------------------------------

    if code.startswith("42"):
        code = "51" + code[2:]

    elif code.startswith("45"):
        code = "52" + code[2:]


    # --------------------------------------------------------
    # 군위군
    #
    # 47720 → 27720
    # --------------------------------------------------------

    if code.startswith("47720"):

        code = "27720" + code[5:]


    return code


# ============================================================
# 시도 코드
# ============================================================

SIDO_NAMES = {
    "11": "서울특별시",
    "21": "부산광역시",
    "22": "대구광역시",
    "23": "인천광역시",
    "24": "광주광역시",
    "25": "대전광역시",
    "26": "울산광역시",
    "27": "세종특별자치시",
    "28": "경기도",
    "29": "강원특별자치도",
    "30": "충청북도",
    "31": "충청남도",
    "32": "전북특별자치도",
    "33": "전라남도",
    "34": "경상북도",
    "35": "경상남도",
    "36": "제주특별자치도",
    "37": "전남광주통합특별시"
}


# ============================================================
# GeoJSON의 코드 찾기
# ============================================================

def geo_feature_code(feature):

    props = feature.get(
        "properties",
        {}
    )

    candidates = [
        props.get("adm_cd2"),
        props.get("adm_cd"),
        props.get("adm_cd8"),
        props.get("SIG_CD"),
        props.get("sig_cd")
    ]

    for value in candidates:

        if value is None:
            continue

        code = re.sub(
            r"\D",
            "",
            str(value)
        )

        if len(code) >= 5:
            return code

    return ""


# ============================================================
# GeoJSON에서 시군구 경계 만들기
# ============================================================

def get_sgg_code(code):

    code = str(code)

    code = normalize_code(code)

    if len(code) >= 5:
        return code[:5]

    return ""


def geometry_to_lists(geometry):

    if not geometry:
        return []

    gtype = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if gtype == "Polygon":

        return [
            [ring for ring in coordinates]
        ]

    if gtype == "MultiPolygon":

        return coordinates

    return []


def merge_geojson_to_sgg(geojson):

    grouped = defaultdict(list)

    for feature in geojson["features"]:

        code = geo_feature_code(
            feature
        )

        sgg_code = get_sgg_code(
            code
        )

        if not sgg_code:
            continue

        geometry = feature.get(
            "geometry"
        )

        if not geometry:
            continue

        polygons = geometry_to_lists(
            geometry
        )

        grouped[sgg_code].extend(
            polygons
        )

    features = []

    for code, polygons in grouped.items():

        features.append({
            "type": "Feature",
            "properties": {
                "sgg_code": code
            },
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    polygons
                ]
            }
        })

    return {
        "type": "FeatureCollection",
        "features": features
    }


# ============================================================
# 연령 컬럼 찾기
# ============================================================

def find_age_columns(df):

    columns = []

    for col in df.columns:

        text = str(col)

        if "계" not in text:
            continue

        match = re.search(
            r"(\d+)세",
            text
        )

        if match:

            age = int(
                match.group(1)
            )

            columns.append(
                (age, col)
            )

        elif "100세 이상" in text:

            columns.append(
                (100, col)
            )

    return sorted(
        columns,
        key=lambda x: x[0]
    )


# ============================================================
# 데이터 처리
# ============================================================

@st.cache_data
def process_year(path):

    df = read_population_csv(
        path
    )

    # 행정구역 컬럼
    area_col = None

    for col in df.columns:

        if "행정구역" in str(col):

            area_col = col
            break

    if area_col is None:

        area_col = df.columns[0]


    # 연령 컬럼
    age_columns = find_age_columns(
        df
    )

    if not age_columns:

        raise ValueError(
            "연령별 인구 컬럼을 찾지 못했습니다."
        )


    # --------------------------------------------------------
    # 지역별 코드
    # --------------------------------------------------------

    result = pd.DataFrame()

    result["행정구역"] = (
        df[area_col]
        .astype(str)
    )

    result["code_raw"] = (
        result["행정구역"]
        .apply(extract_code)
    )

    result["code"] = (
        result["code_raw"]
        .apply(normalize_code)
    )


    # --------------------------------------------------------
    # 시군구 코드
    # --------------------------------------------------------

    result["sgg_code"] = (
        result["code"]
        .apply(
            lambda x:
            x[:5]
            if len(x) >= 5
            else ""
        )
    )


    # --------------------------------------------------------
    # 전체 인구
    # --------------------------------------------------------

    result["전체인구"] = 0.0

    for age, col in age_columns:

        result["전체인구"] += (
            df[col]
            .apply(number)
        )


    # --------------------------------------------------------
    # 0~14세
    # --------------------------------------------------------

    result["유소년인구"] = 0.0

    for age, col in age_columns:

        if 0 <= age <= 14:

            result["유소년인구"] += (
                df[col]
                .apply(number)
            )


    # --------------------------------------------------------
    # 65세 이상
    # --------------------------------------------------------

    result["고령인구"] = 0.0

    for age, col in age_columns:

        if age >= 65:

            result["고령인구"] += (
                df[col]
                .apply(number)
            )


    # --------------------------------------------------------
    # 비율
    # --------------------------------------------------------

    result["고령화율"] = (
        result["고령인구"]
        / result["전체인구"]
        * 100
    )

    result["유소년율"] = (
        result["유소년인구"]
        / result["전체인구"]
        * 100
    )


    result = result[
        result["sgg_code"] != ""
    ].copy()


    # --------------------------------------------------------
    # 같은 시군구의 읍면동 자료를 합산
    # --------------------------------------------------------

    grouped = (
        result
        .groupby("sgg_code")
        .agg(
            전체인구=("전체인구", "sum"),
            고령인구=("고령인구", "sum"),
            유소년인구=("유소년인구", "sum")
        )
        .reset_index()
    )


    grouped["고령화율"] = (
        grouped["고령인구"]
        / grouped["전체인구"]
        * 100
    )

    grouped["유소년율"] = (
        grouped["유소년인구"]
        / grouped["전체인구"]
        * 100
    )


    return grouped


# ============================================================
# 시도 코드 추출
# ============================================================

def sido_from_sgg(code):

    code = str(code)

    if len(code) >= 2:

        return code[:2]

    return ""


# ============================================================
# 시도 이름
# ============================================================

def sido_name(code):

    return SIDO_NAMES.get(
        str(code),
        f"시도 {code}"
    )


# ============================================================
# 시군구 이름 만들기
# ============================================================

def extract_region_name(text):

    text = str(text)

    text = re.sub(
        r"\s*\([^)]*\)",
        "",
        text
    )

    return text.strip()


# ============================================================
# 대표 지역명 찾기
# ============================================================

@st.cache_data
def make_region_names(path):

    df = read_population_csv(
        path
    )

    area_col = None

    for col in df.columns:

        if "행정구역" in str(col):

            area_col = col
            break

    if area_col is None:

        area_col = df.columns[0]


    mapping = {}

    for value in df[area_col]:

        text = str(value)

        code = extract_code(
            text
        )

        code = normalize_code(
            code
        )

        if len(code) >= 5:

            sgg = code[:5]

            if sgg not in mapping:

                clean = extract_region_name(
                    text
                )

                # 읍면동이 아니라
                # 시군구 이름을 우선적으로 사용
                parts = clean.split()

                if len(parts) >= 2:

                    if parts[-1].endswith(
                        ("시", "군", "구")
                    ):

                        mapping[sgg] = (
                            parts[-1]
                        )

    return mapping


# ============================================================
# 데이터 병합
# ============================================================

@st.cache_data
def prepare_year_data(
    path,
    geojson
):

    population = process_year(
        path
    )

    names = make_region_names(
        path
    )

    # 지도 경계
    sgg_geojson = merge_geojson_to_sgg(
        geojson
    )


    # 코드 기준으로 이름/통계 연결
    data_dict = {}

    for _, row in population.iterrows():

        code = str(
            row["sgg_code"]
        )

        data_dict[code] = {
            "전체인구": row["전체인구"],
            "고령인구": row["고령인구"],
            "유소년인구": row["유소년인구"],
            "고령화율": row["고령화율"],
            "유소년율": row["유소년율"],
            "이름": names.get(
                code,
                code
            )
        }


    for feature in sgg_geojson[
        "features"
    ]:

        props = feature[
            "properties"
        ]

        code = props[
            "sgg_code"
        ]

        info = data_dict.get(
            code
        )

        if info:

            props.update(info)

            props["시도코드"] = (
                sido_from_sgg(code)
            )

        else:

            props["전체인구"] = None
            props["고령인구"] = None
            props["유소년인구"] = None
            props["고령화율"] = None
            props["유소년율"] = None
            props["이름"] = code
            props["시도코드"] = (
                sido_from_sgg(code)
            )


    return (
        sgg_geojson,
        population,
        data_dict
    )


# ============================================================
# 지도용 색상
# ============================================================

def old_color(value):

    if value is None:
        return "#D1D5DB"

    if value < 19:
        return "#FFF7BC"

    if value < 23:
        return "#FEC44F"

    if value < 28:
        return "#FE9929"

    if value < 38:
        return "#EC7014"

    return "#990000"


def young_color(value):

    if value is None:
        return "#D1D5DB"

    # 유소년율은 고령화율보다 낮기 때문에
    # 별도의 구간 사용
    if value < 5:
        return "#EFF6FF"

    if value < 7:
        return "#BFDBFE"

    if value < 9:
        return "#93C5FD"

    if value < 11:
        return "#60A5FA"

    return "#2563EB"


# ============================================================
# 지도 SVG
# ============================================================

def create_svg(
    geojson,
    metric,
    selected_sido
):

    all_points = []


    def collect_coords(coords):

        if not isinstance(
            coords,
            list
        ):
            return

        if (
            len(coords) >= 2
            and isinstance(
                coords[0],
                (int, float)
            )
            and isinstance(
                coords[1],
                (int, float)
            )
        ):

            all_points.append(
                (
                    coords[0],
                    coords[1]
                )
            )

        else:

            for item in coords:

                collect_coords(item)


    visible_features = []


    for feature in geojson[
        "features"
    ]:

        props = feature[
            "properties"
        ]

        if (
            selected_sido != "전국"
            and props.get(
                "시도코드"
            ) != selected_sido
        ):
            continue

        visible_features.append(
            feature
        )

        geometry = feature.get(
            "geometry"
        )

        if geometry:

            collect_coords(
                geometry.get(
                    "coordinates",
                    []
                )
            )


    if not all_points:

        return None


    min_x = min(
        p[0]
        for p in all_points
    )

    max_x = max(
        p[0]
        for p in all_points
    )

    min_y = min(
        p[1]
        for p in all_points
    )

    max_y = max(
        p[1]
        for p in all_points
    )


    width = 1000
    height = 720
    padding = 30


    dx = max_x - min_x
    dy = max_y - min_y


    if dx == 0:
        dx = 1

    if dy == 0:
        dy = 1


    scale_x = (
        width - padding * 2
    ) / dx

    scale_y = (
        height - padding * 2
    ) / dy


    scale = min(
        scale_x,
        scale_y
    )


    def project(x, y):

        px = (
            padding
            + (x - min_x)
            * scale
        )

        py = (
            height
            - padding
            - (y - min_y)
            * scale
        )

        return px, py


    def polygon_path(
        polygon
    ):

        if not polygon:
            return ""

        path = []

        for i, point in enumerate(
            polygon
        ):

            x, y = project(
                point[0],
                point[1]
            )

            if i == 0:

                path.append(
                    f"M{x:.2f},{y:.2f}"
                )

            else:

                path.append(
                    f"L{x:.2f},{y:.2f}"
                )

        path.append("Z")

        return "".join(path)


    def geometry_paths(
        geometry
    ):

        if not geometry:
            return []

        gtype = geometry.get(
            "type"
        )

        coords = geometry.get(
            "coordinates"
        )

        paths = []

        if gtype == "Polygon":

            for polygon in coords:

                paths.append(
                    polygon_path(
                        polygon
                    )
                )

        elif gtype == "MultiPolygon":

            for polygon_group in coords:

                for polygon in polygon_group:

                    paths.append(
                        polygon_path(
                            polygon
                        )
                    )

        return paths


    svg = f"""
    <svg
        viewBox="0 0 {width} {height}"
        width="100%"
        xmlns="http://www.w3.org/2000/svg"
        style="
            background:#f8fafc;
            border:1px solid #e5e7eb;
            border-radius:18px;
        "
    >
    """


    for feature in visible_features:

        props = feature[
            "properties"
        ]

        if metric == "고령화율":

            value = props.get(
                "고령화율"
            )

            color = old_color(
                value
            )

            metric_text = (
                "고령화율"
            )

        else:

            value = props.get(
                "유소년율"
            )

            color = young_color(
                value
            )

            metric_text = (
                "유소년율"
            )


        name = props.get(
            "이름",
            props.get(
                "sgg_code",
                "알 수 없음"
            )
        )


        if value is None:

            tooltip = (
                f"{name}: 데이터 없음"
            )

        else:

            tooltip = (
                f"{name} · "
                f"{metric_text} "
                f"{value:.1f}%"
            )


        for path in geometry_paths(
            feature.get(
                "geometry"
            )
        ):

            safe = (
                tooltip
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

            svg += f"""
            <path
                d="{path}"
                fill="{color}"
                stroke="#ffffff"
                stroke-width="0.7"
                style="cursor:pointer;"
            >
                <title>{safe}</title>
            </path>
            """


    svg += "</svg>"

    return svg


# ============================================================
# 데이터 로드
# ============================================================

try:

    geojson = load_geojson()

except Exception as e:

    st.error(
        "행정구역 경계 데이터를 불러오지 못했습니다."
    )

    st.code(
        str(e)
    )

    st.stop()


# ============================================================
# 사이드바
# ============================================================

st.sidebar.header(
    "⚙️ 지도 설정"
)


# ------------------------------------------------------------
# 연도
# ------------------------------------------------------------

years = list(
    population_files.keys()
)

year_min = min(years)
year_max = max(years)


if year_min == year_max:

    selected_year = st.sidebar.slider(
        "📅 연도",
        min_value=year_min,
        max_value=year_max,
        value=year_min
    )

else:

    selected_year = st.sidebar.slider(
        "📅 연도",
        min_value=year_min,
        max_value=year_max,
        value=year_max,
        step=1
    )


# ------------------------------------------------------------
# 지표
# ------------------------------------------------------------

metric = st.sidebar.selectbox(
    "📊 지표",
    [
        "고령화율",
        "유소년율"
    ]
)


# ------------------------------------------------------------
# 시도
# ------------------------------------------------------------

sido_options = [
    ("전국", "전국")
]

for code, name in SIDO_NAMES.items():

    sido_options.append(
        (name, code)
    )


sido_labels = [
    x[0]
    for x in sido_options
]


selected_sido_label = st.sidebar.selectbox(
    "📍 시도 선택",
    sido_labels
)


selected_sido = dict(
    sido_options
)[selected_sido_label]


# ============================================================
# 선택 연도 데이터
# ============================================================

current_file = population_files[
    selected_year
]


try:

    (
        map_data,
        population,
        data_dict
    ) = prepare_year_data(
        current_file,
        geojson
    )

except Exception as e:

    st.error(
        f"{selected_year}년 데이터를 처리하지 못했습니다."
    )

    st.code(
        str(e)
    )

    st.stop()


# ============================================================
# 지도용 데이터 필터
# ============================================================

valid_rows = []


for feature in map_data[
    "features"
]:

    props = feature[
        "properties"
    ]

    if (
        selected_sido != "전국"
        and props.get(
            "시도코드"
        ) != selected_sido
    ):
        continue


    if metric == "고령화율":

        value = props.get(
            "고령화율"
        )

    else:

        value = props.get(
            "유소년율"
        )


    if value is not None:

        valid_rows.append(
            props
        )


# ============================================================
# 지표 카드
# ============================================================

if valid_rows:

    total_population = sum(
        x.get(
            "전체인구",
            0
        )
        or 0
        for x in valid_rows
    )

    total_old = sum(
        x.get(
            "고령인구",
            0
        )
        or 0
        for x in valid_rows
    )

    total_young = sum(
        x.get(
            "유소년인구",
            0
        )
        or 0
        for x in valid_rows
    )


    if metric == "고령화율":

        national_rate = (
            total_old
            / total_population
            * 100
            if total_population
            else 0
        )

    else:

        national_rate = (
            total_young
            / total_population
            * 100
            if total_population
            else 0
        )


    highest = max(
        valid_rows,
        key=lambda x:
        x.get(
            "고령화율"
            if metric == "고령화율"
            else "유소년율"
        )
        or -1
    )


    lowest = min(
        valid_rows,
        key=lambda x:
        x.get(
            "고령화율"
            if metric == "고령화율"
            else "유소년율"
        )
        or 999
    )


    value_key = (
        "고령화율"
        if metric == "고령화율"
        else "유소년율"
    )


    c1, c2, c3 = st.columns(3)


    with c1:

        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">
                    {selected_year}년
                    {'전국' if selected_sido == '전국' else selected_sido_label}
                    {metric}
                </div>

                <div class="metric-value">
                    {national_rate:.1f}%
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )


    with c2:

        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">
                    가장 높은 시군구
                </div>

                <div class="metric-value">
                    {highest.get('이름', '-')}
                </div>

                <div class="metric-name">
                    {highest.get(value_key, 0):.1f}%
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )


    with c3:

        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">
                    가장 낮은 시군구
                </div>

                <div class="metric-value">
                    {lowest.get('이름', '-')}
                </div>

                <div class="metric-name">
                    {lowest.get(value_key, 0):.1f}%
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )


# ============================================================
# 지도 제목
# ============================================================

st.markdown("")


if selected_sido == "전국":

    map_title = "전국"

else:

    map_title = selected_sido_label


st.subheader(
    f"📍 {selected_year}년 {map_title} · {metric}"
)


# ============================================================
# 지도
# ============================================================

svg = create_svg(
    map_data,
    metric,
    selected_sido
)


if svg:

    st.components.v1.html(
        svg,
        height=750,
        scrolling=False
    )

else:

    st.warning(
        "선택한 지역의 지도 데이터를 찾을 수 없습니다."
    )


# ============================================================
# 범례
# ============================================================

st.markdown(
    "### 🎨 색상 범례"
)


if metric == "고령화율":

    st.markdown(
        """
        <div style="
            display:flex;
            flex-wrap:wrap;
            gap:8px;
            align-items:center;
        ">

        <span style="
            background:#FFF7BC;
            padding:7px 12px;
            border-radius:8px;
        ">19% 미만</span>

        <span style="
            background:#FEC44F;
            padding:7px 12px;
            border-radius:8px;
        ">19~23%</span>

        <span style="
            background:#FE9929;
            padding:7px 12px;
            border-radius:8px;
        ">23~28%</span>

        <span style="
            background:#EC7014;
            padding:7px 12px;
            border-radius:8px;
        ">28~38%</span>

        <span style="
            background:#990000;
            color:white;
            padding:7px 12px;
            border-radius:8px;
        ">38% 이상</span>

        <span style="
            background:#D1D5DB;
            padding:7px 12px;
            border-radius:8px;
        ">데이터 없음</span>

        </div>
        """,
        unsafe_allow_html=True
    )

else:

    st.markdown(
        """
        <div style="
            display:flex;
            flex-wrap:wrap;
            gap:8px;
            align-items:center;
        ">

        <span style="
            background:#EFF6FF;
            padding:7px 12px;
            border-radius:8px;
        ">5% 미만</span>

        <span style="
            background:#BFDBFE;
            padding:7px 12px;
            border-radius:8px;
        ">5~7%</span>

        <span style="
            background:#93C5FD;
            padding:7px 12px;
            border-radius:8px;
        ">7~9%</span>

        <span style="
            background:#60A5FA;
            padding:7px 12px;
            border-radius:8px;
        ">9~11%</span>

        <span style="
            background:#2563EB;
            color:white;
            padding:7px 12px;
            border-radius:8px;
        ">11% 이상</span>

        <span style="
            background:#D1D5DB;
            padding:7px 12px;
            border-radius:8px;
        ">데이터 없음</span>

        </div>
        """,
        unsafe_allow_html=True
    )


# ============================================================
# 코드 불일치 안내
# ============================================================

missing_count = 0

for feature in map_data[
    "features"
]:

    props = feature[
        "properties"
    ]

    if (
        selected_sido != "전국"
        and props.get(
            "시도코드"
        ) != selected_sido
    ):
        continue


    if metric == "고령화율":

        value = props.get(
            "고령화율"
        )

    else:

        value = props.get(
            "유소년율"
        )


    if value is None:

        missing_count += 1


if missing_count > 0:

    st.info(
        f"ℹ️ 일부 지역({missing_count}개)은 "
        "연도별 행정구역 개편으로 인해 "
        "현재 경계 코드와 일치하지 않아 회색으로 표시했습니다."
        " 코드 보정(42→51, 45→52, 47720→27720)을 적용했으며, "
        "그래도 일치하지 않는 지역은 데이터 없음으로 처리했습니다."
    )


# ============================================================
# 설명
# ============================================================

st.markdown("---")

st.markdown(
    f"""
### 📌 지도 읽는 법

- **현재 연도:** {selected_year}년
- **현재 지표:** {metric}
- **현재 지역:** {map_title}

연도를 변경하면 해당 연도의 인구 자료로 지도가 다시 계산됩니다.

고령화율은 모든 연도에서 동일하게
**19% · 23% · 28% · 38%** 경계를 사용하므로
연도별 색상을 직접 비교할 수 있습니다.

유소년율은 고령화율보다 값의 범위가 작기 때문에
별도의 색 구간을 사용합니다.
"""
)


# ============================================================
# 데이터 표
# ============================================================

with st.expander(
    "📊 현재 연도 시군구 데이터 보기"
):

    table = pd.DataFrame(
        valid_rows
    )

    if len(table) > 0:

        table = table[
            [
                "이름",
                "전체인구",
                "고령인구",
                "고령화율",
                "유소년인구",
                "유소년율"
            ]
        ].copy()

        table = table.rename(
            columns={
                "이름": "시군구",
                "전체인구": "전체 인구",
                "고령인구": "65세 이상",
                "고령화율": "고령화율(%)",
                "유소년인구": "0~14세",
                "유소년율": "유소년율(%)"
            }
        )

        table["전체 인구"] = (
            table["전체 인구"]
            .round()
            .astype(int)
        )

        table["65세 이상"] = (
            table["65세 이상"]
            .round()
            .astype(int)
        )

        table["0~14세"] = (
            table["0~14세"]
            .round()
            .astype(int)
        )

        table["고령화율(%)"] = (
            table["고령화율(%)"]
            .round(2)
        )

        table["유소년율(%)"] = (
            table["유소년율(%)"]
            .round(2)
        )

        st.dataframe(
            table.sort_values(
                "고령화율(%)",
                ascending=False
            ),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# 출처
# ============================================================

st.markdown("---")

st.caption(
    "인구 자료: 행정안전부 주민등록 연령별 인구현황(월간)"
)

st.caption(
    "행정구역 경계: 통계청 SGIS 기반 "
    "vuski/admdongkor"
)
