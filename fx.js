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
    var TINTS = ['255,150,190', '178,140,240', '110,215,195', '255,190,140'];
    var AURORA = [
        { c: '255,182,203', x: 0.15, y: 0.2, r: 0.45, sx: 0.00021, sy: 0.00017, p: 0 },
        { c: '196,170,240', x: 0.85, y: 0.15, r: 0.5, sx: 0.00017, sy: 0.00023, p: 2 },
        { c: '160,230,215', x: 0.6, y: 0.95, r: 0.55, sx: 0.00019, sy: 0.00015, p: 4 },
        { c: '255,214,170', x: 0.1, y: 0.85, r: 0.4, sx: 0.00023, sy: 0.00019, p: 1 }
    ];

    function newBubble(fromBottom) {
        var r = Math.random() * 30 + 8;
        return {
            x: Math.random() * W, y: fromBottom ? H + r + Math.random() * 80 : Math.random() * H,
            r: r, vy: -(Math.random() * 0.35 + 0.12), ph: Math.random() * 6.28,
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
    }

    function drawBubble(q, now) {
        var wob = Math.sin(now * 0.0009 + q.ph) * 14;
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

        for (var i = 0; i < bubbles.length; i++) {
            var q = bubbles[i];
            var dx = q.x - mouse.x, dy = q.y - mouse.y, dm = Math.sqrt(dx * dx + dy * dy);
            if (dm < q.r + 90 && dm > 0) { q.x += (dx / dm) * 1.1; q.y += (dy / dm) * 1.1; }
            q.y += q.vy;
            if (q.pop > 0) { q.pop += 0.07; }
            if (q.pop >= 1 || q.y < -q.r - 20) bubbles[i] = newBubble(true);
            else drawBubble(q, now);
        }
        if (!doc.hidden) requestAnimationFrame(frame);
    }

    if (!reduce) {
        resize();
        window.addEventListener('resize', resize);
        doc.addEventListener('visibilitychange', function () { if (!doc.hidden) requestAnimationFrame(frame); });
        doc.addEventListener('pointerdown', function (e) {
            for (var i = 0; i < bubbles.length; i++) {
                var q = bubbles[i], bx = q.x + Math.sin(performance.now() * 0.0009 + q.ph) * 14;
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
    function scan() {
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
