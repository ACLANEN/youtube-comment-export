"""
YouTube Comment Research Tool — by Fynn
"""
import os
import csv
import io
import json
from datetime import datetime

import requests
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp
_transcript_api = YouTubeTranscriptApi()
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

API_KEY = os.getenv("YOUTUBE_API_KEY", "AIzaSyADIpiZxbIQ1fBGuibL7Sqjh_7z8B0pF1g")
BASE_URL = "https://www.googleapis.com/youtube/v3"



def _youtube_get(path: str) -> dict:
    url = f"{BASE_URL}/{path}&key={API_KEY}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _format_duration(iso: str) -> str:
    """PT1H23M45S → 1:23:45"""
    import re
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso)
    if not m:
        return ""
    h, mm, s = m.groups()
    parts = []
    if h: parts.append(h)
    parts.append(mm or "0")
    parts.append(s.zfill(2) if s else "00")
    return ":".join(parts) if len(parts) > 2 else f"{parts[0]}:{parts[1]}"



SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")

@app.route("/api/verify-password", methods=["POST"])
def verify_password():
    if not SITE_PASSWORD:
        return jsonify({"ok": True})
    data = request.get_json()
    if data.get("password") == SITE_PASSWORD:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "密码错误"}), 403

@app.route("/")
def index():
    return render_template("index.html")



@app.route("/api/captions")
def api_captions():
    video_id = request.args.get("video_id", "").strip()
    if not video_id:
        return jsonify({"error": "缺少 video_id"}), 400
    lang = request.args.get("lang", "")
    try:
        if lang:
            transcript = _transcript_api.fetch(video_id, languages=[lang])
        else:
            transcript = _transcript_api.fetch(video_id)
    except Exception as e:
        return jsonify({"error": f"字幕获取失败（该视频可能无字幕）: {str(e)}"}), 404

    segs = list(transcript)
    text = " ".join([s.text for s in segs])
    segments = [{"text": s.text, "start": round(s.start, 1),
                  "duration": round(s.duration, 1)} for s in segs]

    return jsonify({
        "video_id": video_id,
        "language": transcript.language if hasattr(transcript, 'language') else "",
        "text": text,
        "segments": segments,
        "total_segments": len(segments),
    })


@app.route("/test-transcript")
def test_transcript():
    '''批量测试字幕可用性 — 验证 yt-dlp 在 Railway 的成功率'''
    video_ids = request.args.get("ids", "dQw4w9WgXcQ").split(",")[:5]
    results = []

    for vid in video_ids:
        vid = vid.strip()
        if not vid:
            continue
        status = "unknown"
        subtitle_type = "none"
        try:
            opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                subs = info.get("subtitles", {})
                auto_subs = info.get("automatic_captions", {})

                if subs:
                    status = "available"
                    subtitle_type = "manual"
                elif auto_subs:
                    status = "available"
                    subtitle_type = "auto"
                else:
                    status = "unavailable"
                    subtitle_type = "none"
        except Exception as e:
            status = "error"
            subtitle_type = str(e)[:100]

        results.append({
            "video_id": vid,
            "status": status,
            "subtitle_type": subtitle_type,
        })

    available = sum(1 for r in results if r["status"] == "available")
    return jsonify({
        "tested": len(results),
        "available": available,
        "rate": f"{available}/{len(results)}" if results else "0/0",
        "results": results,
    })

@app.route("/api/search")
def api_search():
    keyword = request.args.get("q", "").strip()
    page_token = request.args.get("pageToken", "")
    if not keyword:
        return jsonify({"error": "关键词不能为空"}), 400

    path = f"search?part=snippet&q={keyword}&type=video&maxResults=50"
    if page_token:
        path += f"&pageToken={page_token}"

    try:
        data = _youtube_get(path)
    except Exception as e:
        return jsonify({"error": f"搜索失败: {str(e)}"}), 500

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
            f"videos?part=statistics,contentDetails&id={','.join(video_ids)}"
        )
    except Exception as e:
        return jsonify({"error": f"统计失败: {str(e)}"}), 500

    stats_map = {}
    for item in stats_data.get("items", []):
        s = item.get("statistics", {})
        cd = item.get("contentDetails", {})
        stats_map[item["id"]] = {
            "view_count": int(s.get("viewCount", 0)),
            "comment_count": int(s.get("commentCount", 0)),
            "duration": _format_duration(cd.get("duration", "")),
        }

    for v in videos:
        st = stats_map.get(v["video_id"], {})
        v["view_count"] = st.get("view_count", 0)
        v["comment_count"] = st.get("comment_count", 0)
        v["duration"] = st.get("duration", "")

    videos.sort(key=lambda v: v["view_count"], reverse=True)

    # 全量排序时，如果有多页，标记 nextPageToken
    # 注意：分页搜索会打乱排序，所以这里用全量一次性返回50，翻页时前端再拉新一批
    return jsonify({
        "videos": videos,
        "nextPageToken": data.get("nextPageToken", ""),
    })


def _fetch_comments(vid: str, min_likes: int = 0) -> list[dict]:
    """拉取评论，支持最低点赞过滤"""
    try:
        cdata = _youtube_get(
            f"commentThreads?part=snippet&videoId={vid}&maxResults=150&order=relevance"
        )
    except Exception:
        return []
    comments = []
    for item in cdata.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        likes = top.get("likeCount", 0)
        if likes < min_likes:
            continue
        comments.append({
            "author": top["authorDisplayName"],
            "text": top["textDisplay"],
            "likes": likes,
            "published_at": top["publishedAt"],
        })
    return comments


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """评论预览"""
    data = request.get_json()
    video_ids = data.get("video_ids", [])
    if not video_ids:
        return jsonify({"error": "参数错误"}), 400

    comments = _fetch_comments(video_ids[0])
    return jsonify({"comments": comments, "total": len(comments)})


@app.route("/api/export", methods=["POST"])
def api_export():
    data = request.get_json()
    video_ids = data.get("video_ids", [])
    fmt = data.get("format", "csv")
    min_likes = int(data.get("min_likes", 0))

    if not video_ids:
        return jsonify({"error": "请选择至少一个视频"}), 400

    all_rows = []
    stats = {"total_comments": 0, "total_likes": 0, "video_count": 0}

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

        comments = _fetch_comments(vid, min_likes=min_likes)
        if not comments:
            continue

        stats["video_count"] += 1
        for c in comments:
            all_rows.append([
                c["author"], c["text"], c["likes"], c["published_at"],
                title, url, channel, views, pub,
            ])
            stats["total_comments"] += 1
            stats["total_likes"] += c["likes"]

    if not all_rows:
        return jsonify({"error": "所选视频暂无符合条件的评论"}), 404

    # 生成文件名
    first_title = all_rows[0][4][:40].replace(" ", "-").replace("/", "-")
    safe_title = "".join(c if c.isalnum() or c in "-_" else "" for c in first_title)[:40]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{safe_title}-Comments-{date_str}"

    if fmt == "xlsx":
        return _export_xlsx(all_rows, filename, stats)
    elif fmt == "json":
        return _export_json(all_rows, filename, stats)
    else:
        return _export_csv(all_rows, filename, stats)


def _export_csv(rows, filename, stats):
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["评论作者", "评论内容", "点赞数", "评论时间",
                      "视频标题", "视频链接", "频道", "视频播放量", "视频发布时间"])
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{filename}.csv",
    )


def _export_xlsx(rows, filename, stats):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return jsonify({"error": "Excel 导出需要 openpyxl 库"}), 500

    wb = Workbook()
    ws = wb.active
    ws.title = "评论数据"

    headers = ["评论作者", "评论内容", "点赞数", "评论时间",
               "视频标题", "视频链接", "频道", "视频播放量", "视频发布时间"]

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="1a1a1a", end_color="1a1a1a", fill_type="solid")
    header_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        bottom=Side(style="thin", color="E0E0E0")
    )

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align

    for r, row in enumerate(rows, 2):
        for c, val in enumerate(row, 1):
            ws.cell(row=r, column=c, value=val).border = thin_border

    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 40
    ws.column_dimensions["F"].width = 40
    ws.column_dimensions["G"].width = 20
    ws.column_dimensions["H"].width = 14
    ws.column_dimensions["I"].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{filename}.xlsx",
    )


def _export_json(rows, filename, stats):
    result = {
        "exported_at": datetime.now().isoformat(),
        "total_comments": stats["total_comments"],
        "total_likes": stats["total_likes"],
        "video_count": stats["video_count"],
        "comments": []
    }
    for row in rows:
        result["comments"].append({
            "author": row[0], "text": row[1], "likes": row[2],
            "published_at": row[3], "video_title": row[4],
            "video_url": row[5], "channel": row[6],
            "video_views": row[7], "video_published": row[8],
        })
    buf = io.BytesIO()
    buf.write(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{filename}.json",
    )


@app.route("/api/export/stats", methods=["POST"])
def api_export_stats():
    """导出前统计预览"""
    data = request.get_json()
    video_ids = data.get("video_ids", [])
    min_likes = int(data.get("min_likes", 0))

    total_comments = 0
    total_likes = 0
    video_count = 0

    for vid in video_ids:
        comments = _fetch_comments(vid, min_likes=min_likes)
        if comments:
            video_count += 1
            total_comments += len(comments)
            total_likes += sum(c["likes"] for c in comments)

    return jsonify({
        "video_count": video_count,
        "total_comments": total_comments,
        "total_likes": total_likes,
    })



@app.route("/api/export/transcript", methods=["POST"])
def api_export_transcript():
    data = request.get_json()
    video_ids = data.get("video_ids", [])
    fmt = data.get("format", "csv")
    if not video_ids:
        return jsonify({"error": "未选择视频"}), 400

    all_rows = []
    for vid in video_ids:
        try:
            vdata = _youtube_get(f"videos?part=snippet&id={vid}")
        except Exception:
            continue
        items = vdata.get("items", [])
        if not items:
            continue
        title = items[0]["snippet"]["title"]

        try:
            transcript = _transcript_api.fetch(vid)
        except Exception:
            continue

        for seg in transcript:
            start = seg.start
            mm = int(start // 60)
            ss = int(start % 60)
            timestamp = f"{mm:02d}:{ss:02d}"
            link = f"https://www.youtube.com/watch?v={vid}&t={int(start)}"
            all_rows.append([
                title, vid, timestamp, round(seg.duration, 1), seg.text, link
            ])

    if not all_rows:
        return jsonify({"error": "所选视频无字幕"}), 404

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"transcript_{date_str}"

    if fmt == "xlsx":
        try:
            from openpyxl import Workbook; from openpyxl.styles import Font, PatternFill
        except ImportError:
            return jsonify({"error": "需要openpyxl"}), 500
        wb = Workbook(); ws = wb.active; ws.title = "字幕"
        hdrs = ["Video Title", "Video ID", "Timestamp", "Duration", "Transcript", "Video Link"]
        hf = Font(bold=True, color="FFFFFF"); hb = PatternFill(start_color="1a1a1a", end_color="1a1a1a", fill_type="solid")
        for c, h in enumerate(hdrs, 1):
            cell = ws.cell(row=1, column=c, value=h); cell.font = hf; cell.fill = hb
        for r, row in enumerate(all_rows, 2):
            for c, v in enumerate(row, 1): ws.cell(row=r, column=c, value=v)
        ws.column_dimensions["A"].width = 45; ws.column_dimensions["B"].width = 15; ws.column_dimensions["E"].width = 80; ws.column_dimensions["F"].width = 50
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name=f"{filename}.xlsx")
    elif fmt == "json":
        result = {"exported_at": datetime.now().isoformat(), "transcripts": []}
        for r in all_rows:
            result["transcripts"].append(dict(zip(["video_title","video_id","timestamp","duration","text","link"], r)))
        buf = io.BytesIO(); buf.write(json.dumps(result, ensure_ascii=False, indent=2).encode()); buf.seek(0)
        return send_file(buf, mimetype="application/json", as_attachment=True, download_name=f"{filename}.json")
    else:
        buf = io.StringIO(); buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow(["Video Title", "Video ID", "Timestamp", "Duration", "Transcript", "Video Link"])
        for row in all_rows: writer.writerow(row)
        buf.seek(0)
        return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), mimetype="text/csv",
                         as_attachment=True, download_name=f"{filename}.csv")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
