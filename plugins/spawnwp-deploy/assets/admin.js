(function () {
	'use strict';

	const config = window.spawnwpDeployAdmin || {};

	async function requestStep(action, nonce, connection, op, state = {}) {
		const body = new URLSearchParams({ action, nonce, connection, op, ...state });
		const response = await fetch(config.ajaxUrl, {
			method: 'POST',
			credentials: 'same-origin',
			headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
			body
		});
		const data = await response.json();
		if (!data.success) {
			throw new Error(data.data && data.data.message ? data.data.message : 'Operation failed');
		}
		return data.data;
	}

	document.querySelectorAll('.spawnwp-select-on-click').forEach((field) => {
		field.addEventListener('click', () => field.select());
	});

	const deployLog = document.getElementById('spawnwp-log');
	function deployLine(text) {
		if (!deployLog) {
			return;
		}
		deployLog.hidden = false;
		deployLog.textContent += text + '\n';
		deployLog.scrollTop = deployLog.scrollHeight;
	}

	async function deployStep(connection, op, state = {}) {
		return requestStep('spawnwp_deploy_step', config.deployNonce, connection, op, state);
	}

	async function deploy(button) {
		const connection = button.dataset.connection;
		button.disabled = true;
		try {
			deployLine('Checking destination...');
			let data = await deployStep(connection, 'preflight');
			if (data.warnings && data.warnings.length) {
				data.warnings.forEach((warning) => deployLine('WARNING: ' + warning));
				if (!window.confirm(data.warnings.join('\n') + '\n\nContinue anyway?')) {
					throw new Error('Deployment cancelled after compatibility warning.');
				}
			}
			deployLine('Destination ready. Building package...');
			data = await deployStep(connection, 'prepare');
			deployLine('Package ready: ' + data.manifest.archive_bytes + ' bytes');
			while (data.next < data.manifest.chunk_count) {
				data = await deployStep(connection, 'transfer', { job: data.job, next: String(data.next) });
				deployLine('Transferred ' + data.next + ' / ' + data.manifest.chunk_count + ' chunks');
			}
			deployLine('Verifying and staging...');
			await deployStep(connection, 'stage', { job: data.job });
			if (!window.confirm('Final confirmation: this will publish the package to the empty destination. Continue?')) {
				throw new Error('Activation cancelled; staged package retained.');
			}
			deployLine('Activating the verified package...');
			const done = await deployStep(connection, 'activate', { job: data.job });
			deployLine('Deployment ' + done.state + '.');
			button.textContent = 'Deployed';
		} catch (error) {
			deployLine('ERROR: ' + error.message);
			button.disabled = false;
		}
	}

	document.querySelectorAll('.spawnwp-start').forEach((button) => {
		button.addEventListener('click', () => deploy(button));
	});

	document.querySelectorAll('.spawnwp-rollback').forEach((button) => {
		button.addEventListener('click', async () => {
			if (!window.confirm('Rollback the destination to its pre-deploy state?')) {
				return;
			}
			button.disabled = true;
			try {
				deployLine('Rolling back destination...');
				const done = await deployStep(button.dataset.connection, 'rollback', { job: button.dataset.job });
				deployLine('Rollback ' + done.state + '. Reload this page to deploy again.');
			} catch (error) {
				deployLine('ERROR: ' + error.message);
				button.disabled = false;
			}
		});
	});

	const blueprintForm = document.getElementById('spawnwp-blueprint-form');
	const blueprintLog = document.getElementById('spawnwp-bp-log');
	if (!blueprintForm || !blueprintLog) {
		return;
	}
	blueprintForm.addEventListener('submit', (event) => event.preventDefault());
	const premiumCount = Number.parseInt(blueprintForm.dataset.premiumCount || '0', 10);
	const phpPin = blueprintForm.dataset.phpPin || '';

	function blueprintLine(text) {
		blueprintLog.hidden = false;
		blueprintLog.textContent += text + '\n';
		blueprintLog.scrollTop = blueprintLog.scrollHeight;
	}

	function blueprintFields() {
		const allowed = [...document.querySelectorAll('.spawnwp-bp-php:checked')].map((box) => box.value);
		return {
			blueprint_id: document.getElementById('spawnwp-bp-id').value.trim(),
			blueprint_name: document.getElementById('spawnwp-bp-name').value.trim(),
			blueprint_description: document.getElementById('spawnwp-bp-description').value.trim(),
			blueprint_version: document.getElementById('spawnwp-bp-version').value.trim(),
			php_default: document.getElementById('spawnwp-bp-php-default').value,
			php_allowed: allowed.join(','),
			include_plugins: document.getElementById('spawnwp-bp-plugins').checked ? '1' : '',
			include_themes: document.getElementById('spawnwp-bp-themes').checked ? '1' : '',
			include_uploads: document.getElementById('spawnwp-bp-uploads').checked ? '1' : '',
			include_database: document.getElementById('spawnwp-bp-database').checked ? '1' : ''
		};
	}

	async function blueprintStep(connection, op, state = {}) {
		return requestStep('spawnwp_blueprint_step', config.blueprintNonce, connection, op, state);
	}

	const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
	async function capture(button) {
		const connection = button.dataset.connection;
		const fields = blueprintFields();
		if (!blueprintForm.reportValidity()) {
			return;
		}
		button.disabled = true;
		try {
			blueprintLine('Checking SpawnWP server...');
			const preflight = await blueprintStep(connection, 'preflight', { blueprint_id: fields.blueprint_id });
			blueprintLine('Server SpawnWP ' + preflight.spawnwp_version + ' ready.');
			if (preflight.exists) {
				if (!preflight.replaceable) {
					throw new Error('Blueprint id "' + fields.blueprint_id + '" already exists on the server and cannot be replaced. Pick another id.');
				}
				if (!window.confirm('A blueprint named "' + fields.blueprint_id + '" already exists on the server.\nReplace it with this capture? The old version is kept until the new one is verified.')) {
					throw new Error('Capture cancelled.');
				}
				fields.replace = '1';
			}
			if (premiumCount > 0 && !window.confirm('This site uses ' + premiumCount + ' premium/custom plugin(s) (see the warning above).\nSites spawned from this blueprint may require new license keys or re-activation.\n\nContinue?')) {
				throw new Error('Capture cancelled.');
			}
			if (fields.include_database && !window.confirm('The database capture includes this site\'s real posts, pages and settings: they will appear in every site spawned from this blueprint.\nUsers and passwords are never included.\n\nContinue?')) {
				throw new Error('Capture cancelled.');
			}
			blueprintLine('Building capture package (this can take a while)...');
			let data = await blueprintStep(connection, 'prepare', fields);
			const job = data.job;
			blueprintLine('Package ready: ' + data.chunk_count + ' chunks.');
			while (data.next < data.chunk_count) {
				data = await blueprintStep(connection, 'transfer', { job, next: String(data.next) });
				blueprintLine('Transferred ' + data.next + ' / ' + data.chunk_count + ' chunks');
			}
			blueprintLine('Upload complete. Server is verifying and installing...');
			await blueprintStep(connection, 'finalize', { job });
			for (;;) {
				await sleep(2000);
				const status = await blueprintStep(connection, 'status', { job });
				if (status.state === 'complete') {
					blueprintLine('Blueprint installed. It is now available on the Deploy page of your SpawnWP server.');
					button.textContent = 'Blueprint created';
					return;
				}
				if (status.state === 'failed') {
					throw new Error(status.error || 'Server-side installation failed.');
				}
				blueprintLine('Server: ' + status.state + '...');
			}
		} catch (error) {
			blueprintLine('ERROR: ' + error.message);
			button.disabled = false;
		}
	}

	document.querySelectorAll('.spawnwp-bp-start').forEach((button) => {
		button.addEventListener('click', () => capture(button));
	});

	const reset = document.getElementById('spawnwp-bp-reset');
	if (reset) {
		reset.addEventListener('click', () => {
			['spawnwp-bp-id', 'spawnwp-bp-name', 'spawnwp-bp-description'].forEach((id) => {
				document.getElementById(id).value = '';
			});
			document.getElementById('spawnwp-bp-version').value = '1.0.0';
			['spawnwp-bp-plugins', 'spawnwp-bp-themes', 'spawnwp-bp-uploads', 'spawnwp-bp-database'].forEach((id) => {
				document.getElementById(id).checked = true;
			});
			document.querySelectorAll('.spawnwp-bp-php').forEach((box) => {
				box.checked = box.value === phpPin;
			});
			document.getElementById('spawnwp-bp-php-default').value = phpPin;
			const notice = document.getElementById('spawnwp-bp-prefilled');
			if (notice) {
				notice.hidden = true;
			}
			document.getElementById('spawnwp-bp-id').focus();
		});
	}
})();
