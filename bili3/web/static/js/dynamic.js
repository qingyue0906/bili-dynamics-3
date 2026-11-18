(() => {
    const input = document.getElementById('dynamicInput');
    if (!input) return;
    const { api, toast } = window.bili3;
    const previewButton = document.getElementById('previewDynamic');
    const downloadButton = document.getElementById('downloadDynamic');
    const preview = document.getElementById('dynamicPreview');
    let current = null;

    function reset() {
        current = null;
        downloadButton.disabled = true;
        preview.hidden = true;
    }

    input.addEventListener('input', reset);

    previewButton.addEventListener('click', async () => {
        const value = input.value.trim();
        if (!value) return toast('先输入动态链接或 ID', 'error');
        previewButton.disabled = true;
        previewButton.textContent = '获取中...';
        try {
            const data = await api('/api/dynamic/preview', { input: value });
            current = data.post;
            document.getElementById('dynamicAuthor').textContent = data.post.author || data.post.uid;
            document.getElementById('dynamicMeta').textContent =
                `UID ${data.post.uid} · ${data.post.image_count} 张图片` +
                (data.post.is_charge ? ' · 充电专属' : '');
            document.getElementById('dynamicTitle').textContent = data.post.title || '';
            document.getElementById('dynamicTitle').hidden = !data.post.title;
            document.getElementById('dynamicDesc').textContent = data.post.description || '';
            document.getElementById('dynamicDesc').hidden = !data.post.description;
            document.getElementById('dynamicLink').href = data.post.bili_url;
            const avatar = document.getElementById('dynamicAvatar');
            avatar.innerHTML = `<img src="/avatar/${data.post.uid}" alt="">`;
            const thumbs = document.getElementById('dynamicThumbs');
            thumbs.innerHTML = '';
            (data.post.images || []).forEach((image) => {
                const img = document.createElement('img');
                img.src = image.url;
                img.alt = '';
                img.loading = 'lazy';
                img.referrerPolicy = 'no-referrer';
                img.onerror = () => {
                    img.remove();
                    const tile = document.createElement('span');
                    tile.className = 'dynamic-thumb-missing';
                    tile.textContent = '预览不可用';
                    thumbs.appendChild(tile);
                };
                thumbs.appendChild(img);
            });
            preview.hidden = false;
            downloadButton.disabled = false;
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            previewButton.disabled = false;
            previewButton.textContent = '预览';
        }
    });

    downloadButton.addEventListener('click', async () => {
        if (!current) return;
        try {
            await api('/api/dynamic/download', {
                input: input.value.trim(),
                add_user: document.getElementById('addDynamicUser').checked
            });
            toast('下载任务已加入队列', 'ok');
            setTimeout(() => { window.location.href = '/jobs'; }, 350);
        } catch (error) {
            toast(error.message, 'error');
        }
    });
})();
