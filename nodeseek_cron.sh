#!/bin/bash
# NodeSeek daily task - runs via cron
cd /root/inbox
docker exec chromium pip install -q websocket-client 2>/dev/null
docker cp /root/inbox/nodeseek_daily.py chromium:/config/nodeseek_daily.py
RESULT=$(docker exec chromium timeout 60 python3 /config/nodeseek_daily.py 2>&1)
echo "$(date '+%Y-%m-%d %H:%M') NodeSeek: $RESULT" >> /root/inbox/actions.log

# Push notification via 派派
python3 -c "
from reply import send_wx, send_tg, WX_STATE
wx_owner = WX_STATE.get('owner_user_id', '')
msg = '''📅 NodeSeek 每日任务完成
$RESULT'''
send_wx(wx_owner, msg)
send_tg(7712845902, msg)
" 2>/dev/null
