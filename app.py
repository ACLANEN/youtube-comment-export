"""
YouTube 评论搜索 — 纯云端 Web 版
"""
import os
import csv
import io
import traceback
from datetime import datetime

import requests
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

API_KEY = os.getenv("YOUTUBE_API_KEY", "AIzaSyADIpiZxbIQ1fBGuibL7Sqjh_7z8B0pF1g")
BASE_URL = "https://www.googleapis.com/youtube/v3"


def _youtube_get(path: str) -> dict:
    url = f"{BASE_URL}/{path}&key={API_KEY}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/debug")
def debug():
    """调试端点：测试 API Key 是否可用"""
    try:
        data = _youtube_get("search?part=snippet&q=test&type=video&maxResults=1")
        return jsonify({"status": "ok", "api_key_valid": True, "result_count": len(data.get("items", []))})
    except Exception as e:
        return jsonify({"status": "error", "error_type": type(e).__name__, "error": str(e),
                         "traceback": traceback.format_exc()}), 500


@app.route("/api/search")
def api_search():
    keyword = request.args.get("q", "").strip()
    if not keyword:
        return jsonify({"error": "关键词不能为空"}), 400

    try:
        data = _youtube_get(
            f"search?part=snippet&q={keyword}&type=video&maxResults=20"
        )
    except Exception as e:
        return jsonify({"error": f"搜索API失败: {str(e)}"}), 500

    videos = []
    video_ids = []
    for item in data.get("items", []):
        s = item["snippet"]
        vid = item["id"]["videoId"]
        video_ids.append(vid)
        videos.append({
            "video_id": vid,
            "title": s["title"],
            "channel": s["channelTitle"],
            "published_at": s["publishedAt"],
            "thumbnail": s["thumbnails"]["default"]["url"],
        })

    if not video_ids:
        return jsonify({"videos": []})

    try:
        stats_data = _youtube_get(
            f"videos?part=statistics&id={','.join(video_ids)}"
        )
    except Exception as e:
        return jsonify({"error": f"统计API失败: {str(e)}"}), 500

    stats_map = {}
    for item in stats_data.get("items", []):
        s = item.get("statistics", {})
        stats_map[item["id"]] = {
            "view_count": int(s.get("viewCount", 0)),
            "comment_count": int(s.get("commentCount", 0)),
        }

    for v in videos:
        st = stats_map.get(v["video_id"], {})
        v["view_count"] = st.get("view_count", 0)
        v["comment_count"] = st.get("comment_count", 0)

    videos.sort(key=lambda v: v["view_count"], reverse=True)
    return jsonify({"videos": videos})


@app.route("/api/export", methods=["POST"])
def api_export():
    data = request.get_json()
    video_ids = data.get("video_ids", [])
    if not video_ids:
        return jsonify({"error": "请选择至少一个视频"}), 400

    all_rows = []
    for vid in video_ids:
        try:
            vdata = _youtube_get(f"videos?part=snippet,statistics&id={vid}")
        except Exception:
            continue
        items = vdata.get("items", [])
        if not items:
            continue
        info = items[0]
        title = info["snippet"]["title"]
        channel = info["snippet"]["channelTitle"]
        views = int(info.get("statistics", {}).get("viewCount", 0))
        pub = info["snippet"]["publishedAt"]
        url = f"https://www.youtube.com/watch?v={vid}"

        try:
            cdata = _youtube_get(
                f"commentThreads?part=snippet&videoId={vid}&maxResults=150&order=relevance"
            )
        except Exception:
            continue

        for item in cdata.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            all_rows.append([
                top["authorDisplayName"],
                top["textDisplay"],
                top["likeCount"],
                top["publishedAt"],
                title,
                url,
                channel,
                views,
                pub,
            ])

    if not all_rows:
        return jsonify({"error": "所选视频暂无评论或获取失败"}), 404

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["评论作者", "评论内容", "点赞数", "评论时间",
                      "视频标题", "视频链接", "频道", "视频播放量", "视频发布时间"])
    for row in all_rows:
        writer.writerow(row)
    buf.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"youtube_comments_{timestamp}.csv",
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
