(function () {
    if (window.__dekelFx) return;
    window.__dekelFx = true;

    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var doc = document;

    /* ---------- חלקיקים רקע ---------- */
    var canvas = doc.createElement('canvas');
    canvas.id = 'fx-bg';
    doc.body.insertBefore(canvas, doc.body.firstChild);
    var ctx = canvas.getContext('2d');
    var W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var mouse = { x: -9999, y: -9999 };
    var dots = [];
    var COLORS = ['255,111,181', '139,92,246', '94,234,212'];

    function resize() {
        W = window.innerWidth; H = window.innerHeight;
        canvas.width = W * dpr; canvas.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var n = Math.max(26, Math.min(70, Math.round(W / 20)));
        dots = [];
        for (var i = 0; i < n; i++) {
            dots.push({
                x: Math.random() * W, y: Math.random() * H,
                vx: (Math.random() - 0.5) * 0.35, vy: (Math.random() - 0.5) * 0.35,
                r: Math.random() * 1.6 + 0.6,
                c: COLORS[i % COLORS.length]
            });
        }
    }

    function frame() {
        ctx.clearRect(0, 0, W, H);

        if (mouse.x > -999) {
            var g = ctx.createRadialGradient(mouse.x, mouse.y, 0, mouse.x, mouse.y, 220);
            g.addColorStop(0, 'rgba(255,111,181,0.16)');
            g.addColorStop(1, 'rgba(255,111,181,0)');
            ctx.fillStyle = g;
            ctx.fillRect(mouse.x - 220, mouse.y - 220, 440, 440);
        }

        for (var i = 0; i < dots.length; i++) {
            var a = dots[i];
            var dx = a.x - mouse.x, dy = a.y - mouse.y, dm = Math.sqrt(dx * dx + dy * dy);
            if (dm < 140 && dm > 0) { a.x += (dx / dm) * 0.9; a.y += (dy / dm) * 0.9; }
            a.x += a.vx; a.y += a.vy;
            if (a.x < -10) a.x = W + 10; else if (a.x > W + 10) a.x = -10;
            if (a.y < -10) a.y = H + 10; else if (a.y > H + 10) a.y = -10;

            ctx.beginPath();
            ctx.arc(a.x, a.y, a.r, 0, 6.2832);
            ctx.fillStyle = 'rgba(' + a.c + ',0.75)';
            ctx.fill();

            for (var j = i + 1; j < dots.length; j++) {
                var b = dots[j];
                var ddx = a.x - b.x, ddy = a.y - b.y, d = ddx * ddx + ddy * ddy;
                if (d < 16900) {
                    ctx.strokeStyle = 'rgba(' + a.c + ',' + (0.16 * (1 - d / 16900)).toFixed(3) + ')';
                    ctx.lineWidth = 1;
                    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
                }
            }
        }
        if (!doc.hidden) requestAnimationFrame(frame);
    }

    if (!reduce) {
        resize();
        window.addEventListener('resize', resize);
        doc.addEventListener('visibilitychange', function () { if (!doc.hidden) requestAnimationFrame(frame); });
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
    }
    new MutationObserver(scan).observe(doc.body, { childList: true, subtree: true });
    scan();
})();
