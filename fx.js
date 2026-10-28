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
    var ICONS = ['@brush', '@compact', '@puff', '@drop', '@lipstick', '@brush', '@drop'];
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
        var r = Math.random() * 20 + 7;
        return {
            x: Math.random() * W, y: fromBottom ? H + r + Math.random() * 80 : Math.random() * H,
            r: r, vy: -(Math.random() * 0.10 + 0.03), ph: Math.random() * 6.28,
            t: TINTS[Math.floor(Math.random() * TINTS.length)], pop: 0, kind: Math.random() < 0.6 ? 0 : 1, tilt: (Math.random() - 0.5) * 0.8
        };
    }

    function resize() {
        W = window.innerWidth; H = window.innerHeight;
        canvas.width = W * dpr; canvas.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var n = Math.max(5, Math.min(11, Math.round(W / 105)));
        bubbles = [];
        for (var i = 0; i < n; i++) bubbles.push(newBubble(false));
        icons = [];
        var m = Math.max(4, Math.min(8, Math.round(W / 150)));
        for (var j = 0; j < m; j++) icons.push(newIcon(false));
    }

    function newIcon(fromBottom) {
        var size = Math.random() * 22 + 22;
        return {
            e: ICONS[Math.floor(Math.random() * ICONS.length)], s: size,
            x: Math.random() * W, y: fromBottom ? H + size + Math.random() * 120 : Math.random() * H,
            vy: -(Math.random() * 0.09 + 0.03), ph: Math.random() * 6.28, rot: (Math.random() - 0.5) * 0.9,
            a: Math.random() * 0.12 + 0.16
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
            ctx.rotate(0.5);
            pill(-3 * u, 2 * u, 6 * u, 26 * u, 3 * u);
            var hg = ctx.createLinearGradient(-3 * u, 0, 3 * u, 0);
            hg.addColorStop(0, '#D9A8A0'); hg.addColorStop(1, '#8E4A58');
            ctx.fillStyle = hg; ctx.fill();
            pill(-4 * u, -4 * u, 8 * u, 9 * u, 2 * u);
            var fg = ctx.createLinearGradient(-4 * u, 0, 4 * u, 0);
            fg.addColorStop(0, '#E4C9A0'); fg.addColorStop(1, '#B58B5A');
            ctx.fillStyle = fg; ctx.fill();
            ctx.beginPath();
            ctx.moveTo(-4 * u, -3 * u);
            ctx.bezierCurveTo(-9 * u, -10 * u, -7 * u, -22 * u, 0, -26 * u);
            ctx.bezierCurveTo(7 * u, -22 * u, 9 * u, -10 * u, 4 * u, -3 * u);
            ctx.closePath();
            var bg = ctx.createLinearGradient(0, -26 * u, 0, -3 * u);
            bg.addColorStop(0, '#F4E4D6'); bg.addColorStop(1, '#D9A8A0');
            ctx.fillStyle = bg; ctx.fill();
        } else if (kind === '@compact') {
            var cg = ctx.createLinearGradient(-18 * u, -18 * u, 18 * u, 18 * u);
            cg.addColorStop(0, '#F1DDBB'); cg.addColorStop(1, '#B58B5A');
            ctx.beginPath(); ctx.arc(0, 0, 18 * u, 0, 6.2832); ctx.fillStyle = cg; ctx.fill();
            ctx.beginPath(); ctx.arc(0, 0, 12 * u, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.55)'; ctx.fill();
            ctx.lineWidth = 1.4 * u; ctx.strokeStyle = 'rgba(120,80,40,0.45)'; ctx.stroke();
            ctx.beginPath(); ctx.arc(-4 * u, -4 * u, 3 * u, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.85)'; ctx.fill();
        } else if (kind === '@puff') {
            var pg = ctx.createRadialGradient(-4 * u, -4 * u, 2 * u, 0, 0, 18 * u);
            pg.addColorStop(0, '#FBF1E8'); pg.addColorStop(1, '#E6BFB6');
            ctx.beginPath(); ctx.arc(0, 0, 16 * u, 0, 6.2832); ctx.fillStyle = pg; ctx.fill();
            for (var k = 0; k < 8; k++) {
                var a2 = k * 0.785;
                ctx.beginPath(); ctx.arc(Math.cos(a2) * 14 * u, Math.sin(a2) * 14 * u, 4.5 * u, 0, 6.2832);
                ctx.fillStyle = 'rgba(251,241,232,0.9)'; ctx.fill();
            }
            ctx.beginPath(); ctx.arc(0, 0, 5 * u, 0, 6.2832); ctx.fillStyle = '#C98B7A'; ctx.fill();
        } else if (kind === '@lipstick') {
            pill(-6 * u, 4 * u, 12 * u, 20 * u, 2 * u);
            var lg = ctx.createLinearGradient(-6 * u, 0, 6 * u, 0);
            lg.addColorStop(0, '#E4C9A0'); lg.addColorStop(1, '#A87F4E');
            ctx.fillStyle = lg; ctx.fill();
            pill(-5 * u, -6 * u, 10 * u, 11 * u, 2 * u);
            ctx.fillStyle = '#E4C9A0'; ctx.fill();
            ctx.beginPath(); ctx.moveTo(-4.5 * u, -6 * u); ctx.lineTo(4.5 * u, -6 * u); ctx.lineTo(3.5 * u, -20 * u); ctx.lineTo(-2 * u, -23 * u); ctx.closePath();
            ctx.fillStyle = '#A8485A'; ctx.fill();
        } else {
            ctx.beginPath();
            ctx.moveTo(0, -20 * u);
            ctx.bezierCurveTo(14 * u, -2 * u, 17 * u, 6 * u, 17 * u, 10 * u);
            ctx.arc(0, 10 * u, 17 * u, 0, Math.PI, false);
            ctx.bezierCurveTo(-17 * u, 6 * u, -14 * u, -2 * u, 0, -20 * u);
            ctx.closePath();
            var dg = ctx.createLinearGradient(0, -20 * u, 0, 26 * u);
            dg.addColorStop(0, 'rgba(228,201,160,0.9)'); dg.addColorStop(1, 'rgba(201,139,122,0.9)');
            ctx.fillStyle = dg; ctx.fill();
            ctx.beginPath(); ctx.ellipse(-6 * u, 6 * u, 2.5 * u, 6 * u, 0.4, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.6)'; ctx.fill();
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
        var wob = Math.sin(now * 0.0005 + q.ph);
        var x = q.x + wob * 14, y = q.y, r = q.r * (1 + q.pop * 0.5), alpha = (1 - q.pop);
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(wob * 0.28 + q.tilt);
        ctx.globalAlpha = alpha;
        var tint = 'rgba(' + q.t + ',';
        if (q.kind === 0) {
            var w = r * 0.62, h = r * 1.9;
            ctx.beginPath();
            ctx.moveTo(-w, -h); ctx.lineTo(-w, h - w); ctx.arc(0, h - w, w, Math.PI, 0, true); ctx.lineTo(w, -h);
            ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 1.6; ctx.stroke();
            ctx.fillStyle = 'rgba(255,255,255,0.07)'; ctx.fill();
            var level = -h * 0.1 + Math.sin(now * 0.002 + q.ph) * 1.5;
            ctx.beginPath();
            ctx.moveTo(-w + 1, level); ctx.lineTo(-w + 1, h - w); ctx.arc(0, h - w, w - 1, Math.PI, 0, true); ctx.lineTo(w - 1, level); ctx.closePath();
            ctx.fillStyle = tint + '0.38)'; ctx.fill();
            ctx.beginPath(); ctx.moveTo(-w - 3, -h); ctx.lineTo(w + 3, -h); ctx.strokeStyle = 'rgba(255,255,255,0.75)'; ctx.lineWidth = 2; ctx.stroke();
            ctx.beginPath(); ctx.arc(-w * 0.35, level + h * 0.5, 1.6, 0, 6.2832); ctx.arc(w * 0.25, level + h * 0.9, 1.2, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.7)'; ctx.fill();
            ctx.beginPath(); ctx.moveTo(-w * 0.55, -h * 0.7); ctx.lineTo(-w * 0.55, h * 0.1); ctx.strokeStyle = 'rgba(255,255,255,0.45)'; ctx.lineWidth = 1.4; ctx.stroke();
        } else {
            var lr = r * 0.62, gap = r * 0.34;
            ctx.beginPath(); ctx.moveTo(-lr * 2 - gap, 0); ctx.quadraticCurveTo(0, -lr * 1.2, lr * 2 + gap, 0);
            ctx.strokeStyle = 'rgba(255,255,255,0.35)'; ctx.lineWidth = 3; ctx.stroke();
            for (var side = -1; side <= 1; side += 2) {
                var cx = side * (lr + gap * 0.5);
                ctx.beginPath(); ctx.ellipse(cx, 0, lr, lr * 0.8, 0, 0, 6.2832);
                ctx.fillStyle = tint + '0.22)'; ctx.fill();
                ctx.strokeStyle = 'rgba(255,255,255,0.75)'; ctx.lineWidth = 2.4; ctx.stroke();
                ctx.beginPath(); ctx.ellipse(cx - lr * 0.3, -lr * 0.25, lr * 0.28, lr * 0.14, -0.5, 0, 6.2832); ctx.fillStyle = 'rgba(255,255,255,0.55)'; ctx.fill();
            }
            ctx.beginPath(); ctx.moveTo(-gap * 0.6, 0); ctx.lineTo(gap * 0.6, 0); ctx.strokeStyle = 'rgba(255,255,255,0.7)'; ctx.lineWidth = 3; ctx.stroke();
        }
        ctx.restore();
    }

    function frame(now) {
        ctx.clearRect(0, 0, W, H);

        for (var k = 0; k < AURORA.length; k++) {
            var a = AURORA[k];
            var ax = (a.x + Math.sin(now * a.sx + a.p) * 0.12) * W;
            var ay = (a.y + Math.cos(now * a.sy + a.p) * 0.12) * H;
            var ar = a.r * Math.max(W, H);
            var ag = ctx.createRadialGradient(ax, ay, 0, ax, ay, ar);
            ag.addColorStop(0, 'rgba(' + a.c + ',0.30)');
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
    var GLOW = '.hero, .tile, .table-wrap, .rcard, .pcard, .kp, .cp-card, .ug, .personal, .rt-col, .score-card, .g-card, [class*="st-key-go_"] button, [data-testid="stVerticalBlockBorderWrapper"]';
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
    var CONFETTI_COLORS = ['#C9A66B', '#E4C9A0', '#B76E79', '#5B3A6B', '#5F8F83', '#FFFFFF', '#D9B2C0'];

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

    function goldBurst() {
        if (doc.querySelector('.celebrate')) { setTimeout(goldBurst, 700); return; }
        var overlay = makeEl('div', 'cel-mini');
        doc.body.appendChild(overlay);
        launchConfetti(overlay);
        setTimeout(function () { overlay.remove(); }, 4200);
    }

    var burstTries = 0;
    function scoreFx() {
        var card = doc.querySelector('.score-card[data-score]:not([data-fx])');
        if (!card) return;
        card.setAttribute('data-fx', '1');
        var score = parseInt(card.getAttribute('data-score'), 10) || 0;
        if (score >= 85 && !reduce) setTimeout(goldBurst, 900);
    }

    function scan() {
        scoreFx();
        flagAlerts();
        var els = doc.querySelectorAll('.num[data-t]:not([data-done])');
        for (var i = 0; i < els.length; i++) countUp(els[i]);
        celebrate();
    }
    new MutationObserver(scan).observe(doc.body, { childList: true, subtree: true });
    scan();
})();

// שמירת השגרה והפרופיל בדפדפן: הכתובת היא מקור האמת, והדפדפן זוכר אותם לביקור הבא
(function () {
    if (window.__dekelRoutine) return;
    window.__dekelRoutine = true;
    var KEYS = ['routine', 'profile'];
    function read(key) {
        return new URLSearchParams(window.location.search).get(key);
    }
    try {
        var params = new URLSearchParams(window.location.search);
        var restored = false;
        for (var i = 0; i < KEYS.length; i++) {
            var stored = window.localStorage.getItem('dekel_' + KEYS[i]);
            if (!params.get(KEYS[i]) && stored) { params.set(KEYS[i], stored); restored = true; }
        }
        if (restored && !window.sessionStorage.getItem('dekel_restore_tried')) {
            window.sessionStorage.setItem('dekel_restore_tried', '1');
            window.location.replace(window.location.pathname + '?' + params.toString());
            return;
        }
    } catch (e) {}
    setInterval(function () {
        try {
            for (var j = 0; j < KEYS.length; j++) {
                var value = read(KEYS[j]);
                if (value) window.localStorage.setItem('dekel_' + KEYS[j], value);
                else window.localStorage.removeItem('dekel_' + KEYS[j]);
            }
        } catch (e) {}
    }, 1000);
})();
// כפתור החלפת ערכת צבעים בפינת המסך
(function () {
    if (window.__dekelTheme) return;
    window.__dekelTheme = true;
    var THEMES = [['night', 'נואר'], ['pearl', 'אטלייה'], ['emerald', 'אמרלד'], ['blush', 'בלאש'], ['aurora', 'אורורה']];
    var DARK = { night: 1, emerald: 1, aurora: 1 };
    var KEY = 'dekel_theme';
    var root = document.documentElement;
    var index = 0;
    try {
        var saved = window.localStorage.getItem(KEY);
        for (var i = 0; i < THEMES.length; i++) if (THEMES[i][0] === saved) index = i;
    } catch (e) {}

    function apply(show) {
        var name = THEMES[index][0];
        root.setAttribute('data-theme', name);
        if (DARK[name]) root.setAttribute('data-mode', 'dark'); else root.setAttribute('data-mode', 'light');
        mode.textContent = DARK[name] ? '☀️' : '🌙';
        mode.title = DARK[name] ? 'מצב בהיר' : 'מצב כהה';
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
    var mode = document.createElement('button');
    mode.className = 'mode-fab';
    mode.type = 'button';
    mode.setAttribute('aria-label', 'מעבר בין מצב כהה לבהיר');
    var tip = document.createElement('div');
    tip.className = 'theme-tip';
    document.body.appendChild(button);
    document.body.appendChild(mode);
    mode.addEventListener('click', function () {
        var wantDark = !DARK[THEMES[index][0]];
        for (var k = 0; k < THEMES.length; k++) if (!!DARK[THEMES[k][0]] === wantDark) { index = k; break; }
        apply(true);
    });
    document.body.appendChild(tip);
    button.addEventListener('click', function () {
        index = (index + 1) % THEMES.length;
        apply(true);
    });
    apply(false);
})();

// מעבר מסך רך: דהייה קצרה לפני ההחלפה, וכניסה מדורגת אחריה
(function () {
    if (window.__dekelTransitions) return;
    window.__dekelTransitions = true;
    var NAV = '.st-key-nav button, .st-key-bottomnav button, [class*="st-key-go_"] button, [class*="st-key-home_"] button, [class*="st-key-nv_"] button, [class*="st-key-cam_"] button, [class*="st-key-more_"] button';
    var leaving = null;
    document.addEventListener('click', function (e) {
        var hit = e.target.closest && e.target.closest(NAV);
        if (!hit) return;
        var main = document.querySelector('.block-container');
        if (!main) return;
        main.classList.add('leaving');
        leaving = main;
        setTimeout(function () { if (leaving) { leaving.classList.remove('leaving'); leaving = null; } }, 1200);
    }, true);
    new MutationObserver(function () {
        var main = document.querySelector('.block-container');
        if (main && main.classList.contains('leaving') && leaving === main) {
            // התוכן התחלף: מסירים את הדהייה בזמן שהפריטים החדשים נכנסים בהדרגה
            setTimeout(function () { main.classList.remove('leaving'); leaving = null; }, 140);
        }
    }).observe(document.body, { childList: true, subtree: true });
})();

// כניסה לאתר: הבזק זהב, המסך השחור נמוג והאתר נפתח בגל של כניסות
(function () {
    if (window.__dekelEnter) return;
    window.__dekelEnter = true;
    document.addEventListener('click', function (e) {
        var hit = e.target.closest && e.target.closest('.st-key-enter button');
        if (!hit) return;
        var flash = document.createElement('div');
        flash.className = 'gate-flash';
        document.body.appendChild(flash);
        var gate = document.querySelector('.gate');
        if (gate) gate.classList.add('leaving');
        document.documentElement.classList.add('just-entered');
        setTimeout(function () { flash.remove(); }, 1300);
        setTimeout(function () { document.documentElement.classList.remove('just-entered'); }, 4200);
    }, true);
})();

// חשיפה בגלילה: פרקים, ספקטרום ורכיבים נכנסים כשהם מגיעים למסך
(function () {
    if (window.__dekelReveal) return;
    window.__dekelReveal = true;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!('IntersectionObserver' in window) || reduce) {
        document.documentElement.classList.add('no-reveal');
        return;
    }
    var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) { entry.target.classList.add('in'); io.unobserve(entry.target); }
        });
    }, { threshold: 0.18, rootMargin: '0px 0px -6% 0px' });
    function watch() {
        var els = document.querySelectorAll('.reveal:not([data-w])');
        for (var i = 0; i < els.length; i++) { els[i].setAttribute('data-w', '1'); io.observe(els[i]); }
    }
    new MutationObserver(watch).observe(document.body, { childList: true, subtree: true });
    watch();
})();
