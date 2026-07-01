(function () {
    "use strict";

    const form = document.getElementById("generate-form");
    const urlInput = document.getElementById("url");
    const styleInput = document.getElementById("style");
    const stylePills = document.querySelectorAll(".style-pill");
    const generateBtn = document.getElementById("generate-btn");
    const temperatureInput = document.getElementById("temperature");
    const temperatureValue = document.getElementById("temperature-value");
    const btnText = document.getElementById("btn-text");
    const btnSpinner = document.getElementById("btn-spinner");
    const resultSection = document.getElementById("result");
    const resultText = document.getElementById("result-text");
    const copyBtn = document.getElementById("copy-btn");
    const downloadBtn = document.getElementById("download-btn");
    let downloadStatus = document.getElementById("download-status");
    const modalOverlay = document.getElementById("modal-overlay");
    const modalMessage = document.getElementById("modal-message");
    const modalClose = document.getElementById("modal-close");
    const modalDialog = modalOverlay ? modalOverlay.querySelector(".modal") : null;

    if (!form || !urlInput || !styleInput || !modalOverlay || !modalMessage || !modalClose) {
        console.error("Генератор постов: не найдены обязательные элементы DOM.");
        return;
    }

    let currentPost = "";
    let currentStyle = "";
    let downloadStatusTimer = null;
    let downloadBtnTimer = null;
    const defaultBtnLabel = "Сгенерировать пост";
    const defaultDownloadBtnLabel = downloadBtn ? downloadBtn.textContent : "Сохранить в TXT";

    function capitalizeFirst(str) {
        if (!str) return str;
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    function formatTimestampForFilename(date = new Date()) {
        const pad = (n) => String(n).padStart(2, "0");
        const datePart = [
            date.getFullYear(),
            pad(date.getMonth() + 1),
            pad(date.getDate()),
        ].join("");
        const timePart = [
            pad(date.getHours()),
            pad(date.getMinutes()),
            pad(date.getSeconds()),
        ].join("");
        return `${datePart}_${timePart}`;
    }

    function getSelectedStyle() {
        return styleInput.value;
    }

    function selectStyle(style) {
        styleInput.value = style;
        stylePills.forEach((pill) => {
            const isActive = pill.dataset.style === style;
            pill.classList.toggle("style-pill--active", isActive);
            pill.setAttribute("aria-checked", String(isActive));
        });
        updateButtonLabel();
    }

    function updateButtonLabel() {
        const style = getSelectedStyle();
        btnText.textContent = style
            ? `Сгенерировать ${style} пост`
            : defaultBtnLabel;
    }

    function validateForm() {
        const url = urlInput.value.trim();
        const style = getSelectedStyle();

        if (!url) {
            showErrorModal("Введите адрес страницы.");
            return false;
        }

        if (!/^https?:\/\//i.test(url)) {
            showErrorModal("URL должен начинаться с http:// или https://.");
            return false;
        }

        if (!style) {
            showErrorModal("Выберите стиль поста.");
            return false;
        }

        return true;
    }

    function setLoading(isLoading) {
        generateBtn.disabled = isLoading;
        btnSpinner.hidden = !isLoading;
        btnText.textContent = isLoading ? "Генерирую..." : (getSelectedStyle()
            ? `Сгенерировать ${getSelectedStyle()} пост`
            : defaultBtnLabel);
    }

    function showErrorModal(message) {
        modalMessage.textContent = message;
        modalOverlay.classList.add("modal-overlay--open");
        modalOverlay.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
        modalClose.focus();
    }

    function hideErrorModal() {
        modalOverlay.classList.remove("modal-overlay--open");
        modalOverlay.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
    }

    function ensureDownloadStatusElement() {
        if (downloadStatus) {
            return downloadStatus;
        }
        if (!resultSection) {
            return null;
        }

        downloadStatus = document.createElement("p");
        downloadStatus.id = "download-status";
        downloadStatus.className = "result__status";
        downloadStatus.setAttribute("role", "status");
        downloadStatus.setAttribute("aria-live", "polite");
        resultSection.appendChild(downloadStatus);
        return downloadStatus;
    }

    function hideDownloadStatus() {
        if (downloadStatusTimer) {
            clearTimeout(downloadStatusTimer);
            downloadStatusTimer = null;
        }
        if (downloadBtnTimer) {
            clearTimeout(downloadBtnTimer);
            downloadBtnTimer = null;
        }
        if (downloadStatus) {
            downloadStatus.classList.remove("result__status--visible");
            downloadStatus.textContent = "";
        }
        if (downloadBtn) {
            downloadBtn.textContent = defaultDownloadBtnLabel;
        }
    }

    function showDownloadStatus(filename) {
        const statusEl = ensureDownloadStatusElement();
        const message = `Пост сохранён в файл ${filename}`;

        hideDownloadStatus();

        if (statusEl) {
            statusEl.removeAttribute("hidden");
            statusEl.textContent = message;
            statusEl.classList.add("result__status--visible");
            statusEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
            downloadStatusTimer = setTimeout(() => {
                statusEl.classList.remove("result__status--visible");
                statusEl.textContent = "";
                downloadStatusTimer = null;
            }, 15000);
        }

        if (downloadBtn) {
            downloadBtn.textContent = "Сохранено!";
            downloadBtnTimer = setTimeout(() => {
                downloadBtn.textContent = defaultDownloadBtnLabel;
                downloadBtnTimer = null;
            }, 2000);
        }
    }

    function showResult(post) {
        hideDownloadStatus();
        currentPost = post;
        resultText.textContent = post;
        resultSection.hidden = false;
        resultSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function hideResult() {
        resultSection.hidden = true;
        currentPost = "";
        hideDownloadStatus();
    }

    function getGeneratePayload() {
        return {
            url: urlInput.value.trim(),
            style: getSelectedStyle(),
            temperature: parseFloat(temperatureInput.value),
        };
    }

    function updateTemperatureLabel() {
        if (temperatureValue && temperatureInput) {
            temperatureValue.textContent = parseFloat(temperatureInput.value).toFixed(1);
        }
    }

    async function parseJsonResponse(response) {
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
            const text = await response.text();
            throw new Error(
                text
                    ? `сервер вернул не JSON (код ${response.status})`
                    : `сервер вернул пустой ответ (код ${response.status})`
            );
        }

        try {
            return await response.json();
        } catch {
            throw new Error(`сервер вернул некорректный JSON (код ${response.status})`);
        }
    }

    async function handleSubmit(event) {
        event.preventDefault();

        if (!validateForm()) {
            return;
        }

        hideResult();
        setLoading(true);

        try {
            const response = await fetch("/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(getGeneratePayload()),
            });

            const data = await parseJsonResponse(response);

            if (!response.ok) {
                showErrorModal(data.error || "Неизвестная ошибка. Попробуйте позже.");
                return;
            }

            currentStyle = data.style || getSelectedStyle();
            showResult(data.post);
        } catch (error) {
            showErrorModal(`Неизвестная ошибка: ${error.message}. Попробуйте позже.`);
        } finally {
            setLoading(false);
        }
    }

    async function handleCopy() {
        if (!currentPost) return;

        const originalText = copyBtn.textContent;

        try {
            await navigator.clipboard.writeText(currentPost);
            copyBtn.textContent = "Скопировано!";
            setTimeout(() => {
                copyBtn.textContent = originalText;
            }, 2000);
        } catch {
            showErrorModal("Не удалось скопировать текст в буфер обмена.");
        }
    }

    function handleDownload() {
        if (!currentPost) return;

        const styleName = capitalizeFirst(currentStyle || getSelectedStyle() || "post");
        const filename = `${formatTimestampForFilename()}_post_${styleName}.txt`;
        const blob = new Blob([currentPost], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);

        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        requestAnimationFrame(() => {
            showDownloadStatus(filename);
        });
    }

    function handleErrorModalOverlayClick(event) {
        if (event.target === modalOverlay) {
            hideErrorModal();
        }
    }

    function handleKeydown(event) {
        if (event.key === "Escape" && modalOverlay.classList.contains("modal-overlay--open")) {
            hideErrorModal();
        }
    }

    function handleStylePillClick(event) {
        selectStyle(event.currentTarget.dataset.style);
    }

    stylePills.forEach((pill) => {
        pill.addEventListener("click", handleStylePillClick);
    });

    form.addEventListener("submit", handleSubmit);
    if (temperatureInput) {
        temperatureInput.addEventListener("input", updateTemperatureLabel);
    }
    copyBtn.addEventListener("click", handleCopy);
    downloadBtn.addEventListener("click", handleDownload);
    modalClose.addEventListener("click", hideErrorModal);
    modalOverlay.addEventListener("click", handleErrorModalOverlayClick);

    if (modalDialog) {
        modalDialog.addEventListener("click", (event) => event.stopPropagation());
    }

    document.addEventListener("keydown", handleKeydown);
    updateButtonLabel();
    updateTemperatureLabel();
})();
