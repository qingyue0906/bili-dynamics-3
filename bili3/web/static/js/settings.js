(() => {
    const form = document.getElementById('cookieForm');
    if (!form) return;
    const { api, toast } = window.bili3;
    const hint = document.getElementById('cookieHint');

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const data = new FormData(form);
        const payload = {
            sessdata: String(data.get('sessdata') || ''),
            bili_jct: String(data.get('bili_jct') || ''),
            buvid3: String(data.get('buvid3') || '')
        };
        if (!payload.sessdata && !payload.bili_jct && !payload.buvid3) {
            return toast('三个字段都为空，没有修改', 'error');
        }
        try {
            const result = await api('/api/settings/cookies', payload);
            toast(result.message || 'Cookie 已保存', 'ok');
            setTimeout(() => window.location.reload(), 400);
        } catch (error) {
            toast(error.message, 'error');
            if (hint && !hint.dataset.original) {
                hint.dataset.original = hint.textContent;
                hint.textContent = error.message;
            }
        }
    });

    const test = document.getElementById('testCookie');
    if (test) {
        test.addEventListener('click', async () => {
            try {
                const data = await api('/api/settings/cookies/test', {});
                toast(data.message || 'Cookie 有效', 'ok');
            } catch (error) {
                toast(error.message, 'error');
            }
        });
    }

    const clear = document.getElementById('clearCookie');
    if (clear) {
        clear.addEventListener('click', async () => {
            if (!confirm('确定清除当前 Cookie？清除后无法同步。')) return;
            try {
                await api('/api/settings/cookies/clear', {});
                toast('Cookie 已清除', 'ok');
                setTimeout(() => window.location.reload(), 300);
            } catch (error) {
                toast(error.message, 'error');
            }
        });
    }

    const syncOptions = document.getElementById('syncOptionsForm');
    if (syncOptions) {
        syncOptions.addEventListener('submit', async (event) => {
            event.preventDefault();
            const data = new FormData(syncOptions);
            const sync = {
                sleep: data.get('sleep'),
                jitter: data.get('jitter'),
                retries: data.get('retries'),
                download_concurrency: data.get('download_concurrency'),
                avatar_sleep: data.get('avatar_sleep'),
                max_pages: data.get('max_pages'),
                max_images: data.get('max_images'),
                export_json: data.get('export_json') === 'on'
            };
            try {
                await api('/api/settings/options', { sync });
                toast('同步参数已保存', 'ok');
            } catch (error) {
                toast(error.message, 'error');
            }
        });
    }

    const webOptions = document.getElementById('webOptionsForm');
    if (webOptions) {
        webOptions.addEventListener('submit', async (event) => {
            event.preventDefault();
            const data = new FormData(webOptions);
            const web = {
                page_size: data.get('page_size'),
                image_page_size: data.get('image_page_size'),
                default_view: data.get('default_view'),
                default_sort: data.get('default_sort'),
                default_load: data.get('default_load')
            };
            try {
                await api('/api/settings/options', { web });
                toast('显示设置已保存', 'ok');
            } catch (error) {
                toast(error.message, 'error');
            }
        });
    }

    function startJob(url, payload) {
        api(url, payload || {})
            .then(() => {
                toast('任务已加入队列', 'ok');
                setTimeout(() => { window.location.href = '/jobs'; }, 350);
            })
            .catch((error) => toast(error.message, 'error'));
    }

    const mapping = {
        verifyOnly: ['/api/verify', { repair: false }],
        verifyRepair: ['/api/verify', { repair: true }],
        retryAllSettings: ['/api/retry', { all: true }],
        refreshAvatarsSettings: ['/api/avatars/refresh', { all: true }],
        syncAllSettings: ['/api/sync', { all: true }]
    };
    Object.entries(mapping).forEach(([id, [url, payload]]) => {
        const button = document.getElementById(id);
        if (button) button.addEventListener('click', () => startJob(url, payload));
    });
})();
