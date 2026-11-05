
import os
import pandas as pd
import numpy as np
import mysql.connector
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates")

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def connect_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "weather_analysis")
    )


def initialize_sample_data():
    sample_data = [
        ("2026-01-01", "Tirupati", 25.2, 62, 0),
        ("2026-01-02", "Tirupati", 26.1, 59, 0),
        ("2026-01-03", "Tirupati", 24.8, 67, 2.5),
        ("2026-02-01", "Tirupati", 28.4, 55, 0),
        ("2026-02-02", "Tirupati", 29.1, 52, 0),
        ("2026-02-03", "Tirupati", 27.8, 58, 1.2),
        ("2026-03-01", "Tirupati", 32.2, 48, 0),
        ("2026-03-02", "Tirupati", 33.0, 45, 0),
        ("2026-03-03", "Tirupati", 31.5, 50, 0),
        ("2026-01-01", "Chennai", 28.5, 72, 3.0),
        ("2026-01-02", "Chennai", 29.0, 70, 0),
        ("2026-02-01", "Chennai", 30.1, 68, 0),
        ("2026-02-02", "Chennai", 30.5, 67, 4.2),
        ("2026-03-01", "Chennai", 32.0, 65, 0),
        ("2026-03-02", "Chennai", 31.4, 66, 0),
    ]

    conn = connect_db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT COUNT(*) FROM weather_records")
        count = cur.fetchone()[0]

        if count == 0:
            cur.executemany("""
                INSERT INTO weather_records
                (observation_date, location, temperature, humidity, rainfall)
                VALUES (%s, %s, %s, %s, %s)
            """, sample_data)

            conn.commit()
            print("15 sample weather records inserted into MySQL.")
        else:
            print(f"MySQL already contains {count} records. No sample data added.")

    finally:
        cur.close()
        conn.close()

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/status")
def status():
    try:
        conn = connect_db()
        conn.close()
        return jsonify({"connected": True})
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)}), 500


@app.route("/api/locations")
def locations():
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT location FROM weather_records ORDER BY location"
    )
    result = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(result)


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Please choose a CSV file."}), 400

    file = request.files["file"]

    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Upload a CSV file only."}), 400

    try:
        df = pd.read_csv(file)

        # Standardize column names
        df.columns = [
            str(c).strip().lower().replace(" ", "_")
            for c in df.columns
        ]

        aliases = {
            "date": "observation_date",
            "city": "location",
            "temp": "temperature",
            "temp_c": "temperature",
            "temperature_c": "temperature",
            "humidity_percent": "humidity",
            "rainfall_mm": "rainfall",
            "precipitation_mm": "rainfall"
        }
        df.rename(columns=aliases, inplace=True)

        required = [
            "observation_date", "location",
            "temperature", "humidity", "rainfall"
        ]

        missing = [c for c in required if c not in df.columns]
        if missing:
            return jsonify({
                "error": "Missing CSV columns: " + ", ".join(missing)
            }), 400

        df = df[required].copy()
        total = len(df)

        df["observation_date"] = pd.to_datetime(
            df["observation_date"], errors="coerce"
        )

        df["location"] = df["location"].astype("string").str.strip()

        for col in ["temperature", "humidity", "rainfall"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        reasons = []
        valid = []

        for index, row in df.iterrows():
            issues = []

            if pd.isna(row["observation_date"]):
                issues.append("Missing or invalid date")

            if pd.isna(row["location"]) or not str(row["location"]).strip():
                issues.append("Missing location")

            if pd.isna(row["temperature"]):
                issues.append("Missing or invalid temperature")
            elif not -90 <= row["temperature"] <= 65:
                issues.append("Temperature outside -90 to 65 °C")

            if pd.isna(row["humidity"]):
                issues.append("Missing or invalid humidity")
            elif not 0 <= row["humidity"] <= 100:
                issues.append("Humidity outside 0 to 100%")

            if pd.isna(row["rainfall"]):
                issues.append("Missing or invalid rainfall")
            elif row["rainfall"] < 0:
                issues.append("Rainfall cannot be negative")

            if issues:
                reasons.append({
                    "row": int(index) + 2,
                    "issues": "; ".join(issues)
                })
            else:
                valid.append((
                    row["observation_date"].date(),
                    str(row["location"]),
                    float(row["temperature"]),
                    float(row["humidity"]),
                    float(row["rainfall"])
                ))

        if not valid:
            return jsonify({
                "error": "No valid rows to import.",
                "total": total,
                "valid": 0,
                "rejected": len(reasons),
                "issues": reasons
            }), 400

        conn = connect_db()
        
        cur = conn.cursor()

        cur.executemany("""
            INSERT INTO weather_records
            (observation_date, location, temperature, humidity, rainfall)
            VALUES (%s, %s, %s, %s, %s)
        """, valid)

        inserted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "message": "CSV import completed.",
            "total": total,
            "valid": len(valid),
            "inserted": inserted,
            "rejected": len(reasons),
            "issues": reasons
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/summary")
def summary():
    location = request.args.get("location", "")
    start = request.args.get("start", "")
    end = request.args.get("end", "")

    try:
        conn = connect_db()
        cur = conn.cursor(dictionary=True)

        sql = """
            SELECT observation_date, location,
                   temperature, humidity, rainfall
            FROM weather_records WHERE 1=1
        """
        params = []

        if location:
            sql += " AND location = %s"
            params.append(location)

        if start:
            sql += " AND observation_date >= %s"
            params.append(start)

        if end:
            sql += " AND observation_date <= %s"
            params.append(end)

        sql += " ORDER BY observation_date"

        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return jsonify({
                "count": 0, "kpis": {},
                "daily": [], "monthly": [], "records": []
            })

        df = pd.DataFrame(rows)
        df["observation_date"] = pd.to_datetime(
            df["observation_date"]
        )

        df["month"] = df["observation_date"].dt.to_period("M").astype(str)
        df["day"] = df["observation_date"].dt.strftime("%Y-%m-%d")

        monthly = df.groupby("month").agg(
            observations=("temperature", "count"),
            min_temperature=("temperature", "min"),
            avg_temperature=("temperature", "mean"),
            max_temperature=("temperature", "max"),
            avg_humidity=("humidity", "mean"),
            total_rainfall=("rainfall", "sum")
        ).reset_index().round(2)

        daily = df.groupby("day").agg(
            avg_temperature=("temperature", "mean"),
            avg_humidity=("humidity", "mean"),
            total_rainfall=("rainfall", "sum")
        ).reset_index().round(2)

        kpis = {
            "records": int(len(df)),
            "avg_temperature": round(float(df["temperature"].mean()), 2),
            "min_temperature": round(float(df["temperature"].min()), 2),
            "max_temperature": round(float(df["temperature"].max()), 2),
            "avg_humidity": round(float(df["humidity"].mean()), 2),
            "total_rainfall": round(float(df["rainfall"].sum()), 2)
        }

        df["observation_date"] = df["observation_date"].dt.strftime("%Y-%m-%d")

        return jsonify({
            "count": len(df),
            "kpis": kpis,
            "daily": daily.rename(columns={"day": "date"}).to_dict("records"),
            "monthly": monthly.to_dict("records"),
            "records": df.tail(100).iloc[::-1].to_dict("records")
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    try:
        initialize_sample_data()
    except Exception as e:
        print("Database initialization failed:", e)

    app.run(debug=True)