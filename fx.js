(function () {
    if (window.__dekelFx) return;
    window.__dekelFx = true;

    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var doc = document;

    /* ---------- רקע: זוהר צבעוני זז ובועות סרום צפות ---------- */
    var canvas = doc.createElement('canvas');
    canvas.id = 'fx-bg';
    doc.body.insertBefore(canvas, doc.body.firstChild);
    var ctx = canvas.getContext('2d');
    var W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var mouse = { x: -9999, y: -9999 };
    var bubbles = [];
    var icons = [];
    var ICONS = ['💧', '🧴', '🌿', '🌸', '🧪', '🫧', '✨', '🪷', '🍃', '🫙', '💄', '🪞', '💅', '🧼', '🧽', '🌹',
                 '@brush', '@compact', '@brush', '@compact', '@puff'];
    var TINTS = ['255,150,190', '178,140,240', '110,215,195', '255,190,140'];
    function readThemeColors() {
        var cs = window.getComputedStyle(doc.documentElement);
        for (var t = 0; t < 4; t++) {
            var v = cs.getPropertyValue('--aur' + (t + 1)).trim();
            if (v) { AURORA[t].c = v.replace(/\s+/g, ''); TINTS[t] = v.replace(/\s+/g, ''); }
        }
    }
    var AURORA = [
        { c: '255,182,203', x: 0.15, y: 0.2, r: 0.45, sx: 0.00012, sy: 0.0001, p: 0 },
        { c: '196,170,240', x: 0.85, y: 0.15, r: 0.5, sx: 0.0001, sy: 0.00013, p: 2 },
        { c: '160,230,215', x: 0.6, y: 0.95, r: 0.55, sx: 0.00011, sy: 0.00009, p: 4 },
        { c: '255,214,170', x: 0.1, y: 0.85, r: 0.4, sx: 0.00013, sy: 0.00011, p: 1 }
    ];

    window.__dekelReadTheme = function () { readThemeColors(); };
    function newBubble(fromBottom) {
        var r = Math.random() * 30 + 8;
        return {
            x: Math.random() * W, y: fromBottom ? H + r + Math.random() * 80 : Math.random() * H,
            r: r, vy: -(Math.random() * 0.15 + 0.04), ph: Math.random() * 6.28,
            t: TINTS[Math.floor(Math.random() * TINTS.length)], pop: 0
        };
    }

    function resize() {
        W = window.innerWidth; H = window.innerHeight;
        canvas.width = W * dpr; canvas.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var n = Math.max(14, Math.min(34, Math.round(W / 45)));
        bubbles = [];
        for (var i = 0; i < n; i++) bubbles.push(newBubble(false));
        icons = [];
        var m = Math.max(10, Math.min(22, Math.round(W / 62)));
        for (var j = 0; j < m; j++) icons.push(newIcon(false));
    }

    function newIcon(fromBottom) {
        var size = Math.random() * 22 + 22;
        return {
            e: ICONS[Math.floor(Math.random() * ICONS.length)], s: size,
            x: Math.random() * W, y: fromBottom ? H + size + Math.random() * 120 : Math.random() * H,
            vy: -(Math.random() * 0.14 + 0.05), ph: Math.random() * 6.28, rot: (Math.random() - 0.5) * 0.9,
            a: Math.random() * 0.25 + 0.38
        };
    }

    function pill(x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
        ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
        ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
        ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
    }

    function drawCustom(kind, s) {
        var u = s / 40;
        if (kind === '@brush') {
            // מברשת איפור: ידית ורודה, טבעת כסופה וזיפים רכים
            ctx.rotate(0.5);
            pill(-3 * u, 2 * u, 6 * u, 26 * u, 3 * u);
            var hg = ctx.createLinearGradient(-3 * u, 0, 3 * u, 0);
            hg.addColorStop(0, '#F7A8C8'); hg.addColorStop(1, '#D66A9B');
            ctx.fillStyle = hg; ctx.fill();
            pill(-4 * u, -4 * u, 8 * u, 9 * u, 2 * u);
            ctx.fillStyle = '#D8DDE6'; ctx.fill();
            ctx.beginPath();
            ctx.moveTo(-4 * u, -3 * u);
            ctx.bezierCurveTo(-9 * u, -10 * u, -7 * u, -22 * u, 0, -26 * u);
            ctx.bezierCurveTo(7 * u, -22 * u, 9 * u, -10 * u, 4 * u, -3 * u);
            ctx.closePath();
            var bg = ctx.createLinearGradient(0, -26 * u, 0, -3 * u);
            bg.addColorStop(0, '#FFE1D2'); bg.addColorStop(0.6, '#FFC2D6'); bg.addColorStop(1, '#F59BBE');
            ctx.fillStyle = bg; ctx.fill();
        } else if (kind === '@compact') {
            // פודרייה: מכסה עגול עם מראה וצירים
            var cg = ctx.createRadialGradient(-6 * u, -6 * u, 2 * u, 0, 0, 20 * u);
            cg.addColorStop(0, '#FFD9E8'); cg.addColorStop(1, '#F08AB0');
            ctx.beginPath(); ctx.arc(0, 0, 18 * u, 0, 6.2832); ctx.fillStyle = cg; ctx.fill();
            ctx.lineWidth = 2 * u; ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.stroke();
            ctx.beginPath(); ctx.arc(0, 0, 11 * u, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.45)'; ctx.fill();
            ctx.beginPath(); ctx.arc(-4 * u, -4 * u, 3 * u, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.fill();
            ctx.beginPath(); ctx.moveTo(-8 * u, 18 * u); ctx.lineTo(8 * u, 18 * u);
            ctx.lineWidth = 3 * u; ctx.strokeStyle = '#D66A9B'; ctx.stroke();
        } else {
            // ספוג פודרה: ענן רך עם ידית
            var pg = ctx.createRadialGradient(-4 * u, -4 * u, 2 * u, 0, 0, 18 * u);
            pg.addColorStop(0, '#FFF1E6'); pg.addColorStop(1, '#F7B7C9');
            ctx.beginPath(); ctx.arc(0, 0, 16 * u, 0, 6.2832); ctx.fillStyle = pg; ctx.fill();
            for (var k = 0; k < 8; k++) {
                var a = k * 0.785;
                ctx.beginPath(); ctx.arc(Math.cos(a) * 14 * u, Math.sin(a) * 14 * u, 4.5 * u, 0, 6.2832);
                ctx.fillStyle = 'rgba(255,232,238,0.9)'; ctx.fill();
            }
            ctx.beginPath(); ctx.arc(0, 0, 5 * u, 0, 6.2832); ctx.fillStyle = '#E995B2'; ctx.fill();
        }
    }

    function drawIcon(q, now) {
        var sway = Math.sin(now * 0.0005 + q.ph);
        ctx.save();
        ctx.globalAlpha = q.a;
        ctx.translate(q.x + sway * 22, q.y);
        ctx.rotate(sway * 0.35 + q.rot * 0.2);
        if (q.e.charAt(0) === '@') {
            drawCustom(q.e, q.s);
            ctx.restore();
            return;
        }
        ctx.font = q.s + 'px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(q.e, 0, 0);
        ctx.restore();
    }

    function drawBubble(q, now) {
        var wob = Math.sin(now * 0.0005 + q.ph) * 14;
        var x = q.x + wob, y = q.y, r = q.r * (1 + q.pop * 0.5), alpha = 1 - q.pop;
        var g = ctx.createRadialGradient(x - r * 0.35, y - r * 0.4, r * 0.1, x, y, r);
        g.addColorStop(0, 'rgba(255,255,255,' + (0.75 * alpha).toFixed(3) + ')');
        g.addColorStop(0.55, 'rgba(' + q.t + ',' + (0.16 * alpha).toFixed(3) + ')');
        g.addColorStop(1, 'rgba(' + q.t + ',' + (0.34 * alpha).toFixed(3) + ')');
        ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fillStyle = g; ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,' + (0.8 * alpha).toFixed(3) + ')';
        ctx.lineWidth = 1.2; ctx.stroke();
        ctx.beginPath(); ctx.arc(x - r * 0.35, y - r * 0.4, r * 0.18, 0, 6.2832);
        ctx.fillStyle = 'rgba(255,255,255,' + (0.9 * alpha).toFixed(3) + ')'; ctx.fill();
    }

    function frame(now) {
        ctx.clearRect(0, 0, W, H);

        for (var k = 0; k < AURORA.length; k++) {
            var a = AURORA[k];
            var ax = (a.x + Math.sin(now * a.sx + a.p) * 0.12) * W;
            var ay = (a.y + Math.cos(now * a.sy + a.p) * 0.12) * H;
            var ar = a.r * Math.max(W, H);
            var ag = ctx.createRadialGradient(ax, ay, 0, ax, ay, ar);
            ag.addColorStop(0, 'rgba(' + a.c + ',0.34)');
            ag.addColorStop(1, 'rgba(' + a.c + ',0)');
            ctx.fillStyle = ag; ctx.fillRect(0, 0, W, H);
        }

        if (mouse.x > -999) {
            var g = ctx.createRadialGradient(mouse.x, mouse.y, 0, mouse.x, mouse.y, 200);
            g.addColorStop(0, 'rgba(255,150,190,0.20)');
            g.addColorStop(1, 'rgba(255,150,190,0)');
            ctx.fillStyle = g;
            ctx.fillRect(mouse.x - 200, mouse.y - 200, 400, 400);
        }

        for (var m2 = 0; m2 < icons.length; m2++) {
            var ic = icons[m2];
            ic.y += ic.vy;
            if (ic.y < -ic.s - 20) icons[m2] = newIcon(true);
            else drawIcon(ic, now);
        }

        for (var i = 0; i < bubbles.length; i++) {
            var q = bubbles[i];
            var dx = q.x - mouse.x, dy = q.y - mouse.y, dm = Math.sqrt(dx * dx + dy * dy);
            if (dm < q.r + 90 && dm > 0) { q.x += (dx / dm) * 0.6; q.y += (dy / dm) * 0.6; }
            q.y += q.vy;
            if (q.pop > 0) { q.pop += 0.05; }
            if (q.pop >= 1 || q.y < -q.r - 20) bubbles[i] = newBubble(true);
            else drawBubble(q, now);
        }
        if (!doc.hidden) requestAnimationFrame(frame);
    }

    if (!reduce) {
        resize();
        readThemeColors();
        window.addEventListener('resize', resize);
        doc.addEventListener('visibilitychange', function () { if (!doc.hidden) requestAnimationFrame(frame); });
        doc.addEventListener('pointerdown', function (e) {
            for (var i = 0; i < bubbles.length; i++) {
                var q = bubbles[i], bx = q.x + Math.sin(performance.now() * 0.0005 + q.ph) * 14;
                var d = Math.sqrt((bx - e.clientX) * (bx - e.clientX) + (q.y - e.clientY) * (q.y - e.clientY));
                if (d < q.r + 6 && q.pop === 0) q.pop = 0.01;
            }
        }, { passive: true });
        requestAnimationFrame(frame);
    } else {
        canvas.style.display = 'none';
    }

    /* ---------- זוהר שעוקב אחרי העכבר + הטיה תלת-ממדית ---------- */
    var GLOW = '.hero, .tile, .table-wrap, .rcard, [data-testid="stVerticalBlockBorderWrapper"]';
    var TILT = '.hero, .tile';
    var tilted = null;

    doc.addEventListener('pointermove', function (e) {
        mouse.x = e.clientX; mouse.y = e.clientY;
        var glow = e.target.closest && e.target.closest(GLOW);
        if (glow) {
            var r = glow.getBoundingClientRect();
            glow.style.setProperty('--mx', (e.clientX - r.left) + 'px');
            glow.style.setProperty('--my', (e.clientY - r.top) + 'px');
        }
        if (reduce) return;
        var t = e.target.closest && e.target.closest(TILT);
        if (tilted && tilted !== t) tilted.style.transform = '';
        if (t) {
            var tr = t.getBoundingClientRect();
            var px = (e.clientX - tr.left) / tr.width - 0.5;
            var py = (e.clientY - tr.top) / tr.height - 0.5;
            var k = t.classList.contains('hero') ? 4 : 12;
            t.style.transform = 'perspective(800px) rotateX(' + (-py * k).toFixed(2) + 'deg) rotateY(' + (px * k).toFixed(2) + 'deg) translateZ(0)';
        }
        tilted = t;
    }, { passive: true });

    doc.addEventListener('pointerleave', function () {
        mouse.x = mouse.y = -9999;
        if (tilted) { tilted.style.transform = ''; tilted = null; }
    });

    /* ---------- גלים בלחיצה על כפתורים ---------- */
    doc.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest('.stButton > button, .stLinkButton > a');
        if (!btn || reduce) return;
        var r = btn.getBoundingClientRect();
        var s = doc.createElement('span');
        s.className = 'ripple';
        s.style.left = (e.clientX - r.left) + 'px';
        s.style.top = (e.clientY - r.top) + 'px';
        btn.appendChild(s);
        setTimeout(function () { s.remove(); }, 650);
    });

    /* ---------- בניית אלמנטים (בלי מחרוזות HTML) ---------- */
    function makeEl(tag, className, text) {
        var node = doc.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    /* ---------- תמונת מוצר בחלונית גדולה עם זום ---------- */
    var lightbox = null;

    function closeLightbox() {
        if (!lightbox) return;
        var box = lightbox;
        lightbox = null;
        box.classList.remove('open');
        setTimeout(function () { box.remove(); }, reduce ? 0 : 280);
    }

    function openLightbox(img) {
        closeLightbox();
        var card = img.closest('.pcard');
        var nameNode = card ? card.querySelector('.pname') : null;
        var companyNode = card ? card.querySelector('.pco') : null;
        var name = nameNode ? nameNode.textContent : '';
        var company = companyNode ? companyNode.textContent : '';

        var box = makeEl('div', 'lb');
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        var panel = makeEl('div', 'lb-card');
        var closeBtn = makeEl('button', 'lb-close', '✕');
        closeBtn.setAttribute('aria-label', 'close');
        var stage = makeEl('div', 'lb-stage');
        var big = makeEl('img', 'lb-img');
        big.src = img.src;
        big.alt = name;
        stage.appendChild(big);
        var caption = makeEl('div', 'lb-cap');
        caption.appendChild(makeEl('b', '', name));
        caption.appendChild(makeEl('span', '', company));
        var hint = makeEl('div', 'lb-hint', 'לחצי על התמונה כדי להתקרב');
        panel.appendChild(closeBtn);
        panel.appendChild(stage);
        panel.appendChild(caption);
        panel.appendChild(hint);
        box.appendChild(panel);
        doc.body.appendChild(box);
        lightbox = box;
        requestAnimationFrame(function () { box.classList.add('open'); });

        var zoomed = false;
        function setOrigin(e) {
            var r = stage.getBoundingClientRect();
            var x = Math.max(0, Math.min(100, (e.clientX - r.left) / r.width * 100));
            var y = Math.max(0, Math.min(100, (e.clientY - r.top) / r.height * 100));
            big.style.transformOrigin = x + '% ' + y + '%';
        }
        stage.addEventListener('click', function (e) {
            e.stopPropagation();
            zoomed = !zoomed;
            big.classList.toggle('zoomed', zoomed);
            if (zoomed) setOrigin(e);
            hint.textContent = zoomed ? 'מזיזים את העכבר כדי לסייר בתמונה, ולחיצה מחזירה' : 'לחצי על התמונה כדי להתקרב';
        });
        stage.addEventListener('pointermove', function (e) { if (zoomed) setOrigin(e); });
        closeBtn.addEventListener('click', closeLightbox);
        box.addEventListener('click', function (e) { if (e.target === box) closeLightbox(); });
        box.addEventListener('wheel', function (e) { e.preventDefault(); }, { passive: false });
    }

    doc.addEventListener('click', function (e) {
        var img = e.target.closest && e.target.closest('.pcard .pimg');
        if (img) openLightbox(img);
    });
    doc.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeLightbox();
    });

    /* ---------- קונפטי חגיגי ---------- */
    var CONFETTI_COLORS = ['#F08AB0', '#B28CF0', '#6ED7C3', '#FBBF24', '#FFFFFF', '#F472B6', '#7DD3FC'];

    function launchConfetti(host) {
        if (reduce) return;
        var canvas2 = makeEl('canvas', 'cel-confetti');
        host.appendChild(canvas2);
        var w = window.innerWidth, h = window.innerHeight;
        var ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas2.width = w * ratio;
        canvas2.height = h * ratio;
        var c = canvas2.getContext('2d');
        c.scale(ratio, ratio);
        var pieces = [];

        function spawn(x, y, vx, vy) {
            pieces.push({
                x: x, y: y, vx: vx, vy: vy,
                w: 6 + Math.random() * 8, h: 4 + Math.random() * 7,
                rot: Math.random() * 6.28, vr: (Math.random() - 0.5) * 0.4,
                color: CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)],
                round: Math.random() < 0.25,
                sway: Math.random() * 6.28, born: performance.now(),
                life: 2400 + Math.random() * 1600
            });
        }
        function burst(x, y, count, minSpeed, maxSpeed, spread, baseAngle) {
            for (var i = 0; i < count; i++) {
                var angle = baseAngle + (Math.random() - 0.5) * spread;
                var speed = minSpeed + Math.random() * (maxSpeed - minSpeed);
                spawn(x, y, Math.cos(angle) * speed, Math.sin(angle) * speed);
            }
        }

        var start = performance.now();
        var fired = false;
        (function frame(now) {
            var t = now - start;
            if (!fired && t > 120) {
                fired = true;
                burst(w * 0.5, h * 0.5, 90, 7, 18, 6.28, 0);                    // פיצוץ מהמרכז
                burst(w * 0.06, h * 1.02, 60, 16, 28, 0.9, -1.15);             // תותח משמאל
                burst(w * 0.94, h * 1.02, 60, 16, 28, 0.9, -Math.PI + 1.15);   // תותח מימין
            }
            if (t > 400 && t < 2900) {                                           // גשם קונפטי מלמעלה
                for (var k = 0; k < 3; k++) spawn(Math.random() * w, -12, (Math.random() - 0.5) * 1.5, 2 + Math.random() * 3);
            }
            c.clearRect(0, 0, w, h);
            for (var i = pieces.length - 1; i >= 0; i--) {
                var q = pieces[i];
                var age = now - q.born;
                if (age > q.life || q.y > h + 40) { pieces.splice(i, 1); continue; }
                q.vy += 0.22;
                q.vx *= 0.992;
                q.x += q.vx + Math.sin(age / 180 + q.sway) * 0.7;
                q.y += q.vy;
                q.rot += q.vr;
                c.save();
                c.globalAlpha = Math.max(0, Math.min(1, (q.life - age) / 500));
                c.translate(q.x, q.y);
                c.rotate(q.rot);
                c.fillStyle = q.color;
                if (q.round) { c.beginPath(); c.arc(0, 0, q.w / 2.4, 0, 6.28); c.fill(); }
                else { c.fillRect(-q.w / 2, -q.h / 2, q.w, q.h); }
                c.restore();
            }
            if (t < 5200 && (pieces.length || t < 3000) && canvas2.isConnected) requestAnimationFrame(frame);
            else canvas2.remove();
        })(start);
    }

    /* ---------- חגיגת Dekel Verified על כל המסך ---------- */
    var celebrated = {};
    var SPARKS = ['✨', '⭐', '💫'];

    function celebrate() {
        var marker = doc.querySelector('.celebrate-marker');
        if (!marker) return;
        var id = marker.getAttribute('data-id');
        if (!id || celebrated[id]) return;
        celebrated[id] = true;

        var negative = marker.getAttribute('data-kind') === 'no';
        var overlay = makeEl('div', negative ? 'celebrate no' : 'celebrate');
        overlay.appendChild(makeEl('div', 'cel-glow'));
        if (negative) {
            var DROPS = ['✖', '💔', '☁️', '✖'];
            for (var d = 0; d < 26; d++) {
                var drop = makeEl('i', 'cel-drop', DROPS[d % DROPS.length]);
                drop.style.left = (Math.random() * 96 + 2).toFixed(1) + '%';
                drop.style.animationDelay = (Math.random() * 2.2).toFixed(2) + 's';
                drop.style.animationDuration = (2.2 + Math.random() * 1.6).toFixed(2) + 's';
                drop.style.fontSize = (1.1 + Math.random() * 1.6).toFixed(2) + 'rem';
                overlay.appendChild(drop);
            }
        }
        for (var i = 0; i < (negative ? 0 : 14); i++) {
            var spark = makeEl('i', 'cel-spark', SPARKS[i % SPARKS.length]);
            var angle = (i / 14) * Math.PI * 2;
            var dist = 30 + Math.random() * 16;
            spark.style.setProperty('--dx', (Math.cos(angle) * dist).toFixed(1) + 'vmin');
            spark.style.setProperty('--dy', (Math.sin(angle) * dist).toFixed(1) + 'vmin');
            spark.style.animationDelay = (0.2 + Math.random() * 0.3).toFixed(2) + 's';
            overlay.appendChild(spark);
        }
        overlay.appendChild(makeEl('div', 'cel-logo'));
        if (negative) overlay.appendChild(makeEl('div', 'cel-stamp', 'NOT VERIFIED'));
        doc.body.appendChild(overlay);
        if (!negative) launchConfetti(overlay);

        requestAnimationFrame(function () {
            requestAnimationFrame(function () { overlay.classList.add('in'); });
        });
        // נכנס, נשאר 3 שניות, ויוצא באותה דרך בחזרה
        setTimeout(function () { overlay.classList.remove('in'); overlay.classList.add('out'); }, 650 + 3000);
        setTimeout(function () { overlay.remove(); }, 650 + 3000 + 800);
    }

    /* ---------- חזרה למעלה ---------- */
    doc.addEventListener('click', function (e) {
        var link = e.target.closest && e.target.closest('.to-top');
        if (!link) return;
        e.preventDefault();
        var main = doc.querySelector('[data-testid="stMain"]') || window;
        main.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    });

    /* ---------- ספירה עולה במספרים ---------- */
    function countUp(el) {
        if (el.dataset.done) return;
        el.dataset.done = '1';
        var target = parseInt(el.dataset.t, 10) || 0;
        if (reduce || target === 0) { el.textContent = target; return; }
        var start = performance.now(), dur = 1100;
        (function tick(now) {
            var p = Math.min(1, (now - start) / dur);
            el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
            if (p < 1) requestAnimationFrame(tick);
        })(start);
    }
    function flagAlerts() {
        var buttons = doc.querySelectorAll('.st-key-nav button, .st-key-home_history button');
        for (var b = 0; b < buttons.length; b++) {
            buttons[b].classList.toggle('has-alert', buttons[b].textContent.indexOf('🔔') >= 0);
        }
    }

    function scan() {
        flagAlerts();
        var els = doc.querySelectorAll('.num[data-t]:not([data-done])');
        for (var i = 0; i < els.length; i++) countUp(els[i]);
        celebrate();
    }
    new MutationObserver(scan).observe(doc.body, { childList: true, subtree: true });
    scan();
})();

// שמירת השגרה בדפדפן: הכתובת היא מקור האמת, והדפדפן זוכר אותה לביקור הבא
(function () {
    if (window.__dekelRoutine) return;
    window.__dekelRoutine = true;
    var KEY = "dekel_routine";
    function readParam() {
        return new URLSearchParams(window.location.search).get("routine");
    }
    try {
        var stored = window.localStorage.getItem(KEY);
        if (!readParam() && stored && !window.sessionStorage.getItem("dekel_routine_tried")) {
            window.sessionStorage.setItem("dekel_routine_tried", "1");
            var params = new URLSearchParams(window.location.search);
            params.set("routine", stored);
            window.location.replace(window.location.pathname + "?" + params.toString());
            return;
        }
    } catch (e) {}
    setInterval(function () {
        try {
            var value = readParam();
            if (value) window.localStorage.setItem(KEY, value);
            else window.localStorage.removeItem(KEY);
        } catch (e) {}
    }, 1000);
})();

// כפתור החלפת ערכת צבעים בפינת המסך
(function () {
    if (window.__dekelTheme) return;
    window.__dekelTheme = true;
    var THEMES = [['pearl', 'פנינה'], ['mint', 'מנטה'], ['peach', 'אפרסק'], ['night', 'לילה']];
    var KEY = 'dekel_theme';
    var root = document.documentElement;
    var index = 0;
    try {
        var saved = window.localStorage.getItem(KEY);
        for (var i = 0; i < THEMES.length; i++) if (THEMES[i][0] === saved) index = i;
    } catch (e) {}

    function apply(show) {
        var name = THEMES[index][0];
        if (name === 'pearl') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', name);
        try { window.localStorage.setItem(KEY, name); } catch (e) {}
        if (window.__dekelReadTheme) window.__dekelReadTheme();
        if (show) {
            tip.textContent = 'ערכת צבעים: ' + THEMES[index][1];
            tip.classList.add('show');
            clearTimeout(tip._t);
            tip._t = setTimeout(function () { tip.classList.remove('show'); }, 1600);
        }
    }

    var button = document.createElement('button');
    button.className = 'theme-fab';
    button.type = 'button';
    button.title = 'החלפת ערכת צבעים';
    button.setAttribute('aria-label', 'החלפת ערכת צבעים');
    button.textContent = '🎨';
    var tip = document.createElement('div');
    tip.className = 'theme-tip';
    document.body.appendChild(button);
    document.body.appendChild(tip);
    button.addEventListener('click', function () {
        index = (index + 1) % THEMES.length;
        apply(true);
    });
    apply(false);
})();
