from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import uuid
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_from_directory
import google.generativeai as genai
from blockchain_store import get_blockchain
from translator_service import translate_text, text_to_speech, LANGUAGES, cleanup_audio
from licenses import validate_license, VALID_LICENSES

app = Flask(__name__)
app.config['SECRET_KEY'] = 'medicine-chain-secret-key-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///medicine_supply_chain.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'certificates'), exist_ok=True)
os.makedirs('static/audio', exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
CERTIFICATE_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_certificate(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in CERTIFICATE_EXTENSIONS


genai.configure(api_key='AIzaSyA7N2IsdchHxteEgkW81hf0bpgtoUWqPvs')
try:
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
except Exception as e:
    print(f"Gemini init error: {e}")
    gemini_model = None


# ==================== MODELS ====================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    license_number = db.Column(db.String(100), unique=True)
    company_name = db.Column(db.String(150))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    is_approved = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    wallet_balance = db.Column(db.Float, default=100000.0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class MedicineProduct(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    manufacturer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    composition = db.Column(db.String(300))
    category = db.Column(db.String(100))
    batch_number = db.Column(db.String(50))
    mfg_date = db.Column(db.DateTime)
    expiry_date = db.Column(db.DateTime)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='strip')
    price_per_unit = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    uses = db.Column(db.Text)
    side_effects = db.Column(db.Text)
    dosage = db.Column(db.Text)
    storage = db.Column(db.Text)
    prescription_required = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='pending')
    is_dispatched = db.Column(db.Boolean, default=False)
    dispatched_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    image_url = db.Column(db.String(500))
    image_filename = db.Column(db.String(200))
    certificate_id = db.Column(db.Integer, db.ForeignKey('medicine_certificate.id'))
    rating = db.Column(db.Float, default=0.0)
    total_ratings = db.Column(db.Integer, default=0)
    rating_sum = db.Column(db.Float, default=0.0)
    manufacturer = db.relationship('User', foreign_keys=[manufacturer_id])


class PharmacyRequest(db.Model):
    """Pharmacy → Supplier → Manufacturer full workflow"""
    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    manufacturer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    medicine_id = db.Column(db.Integer, db.ForeignKey('medicine_product.id'), nullable=False)
    medicine_name = db.Column(db.String(150))
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='strip')
    expected_price = db.Column(db.Float)
    final_price = db.Column(db.Float)

    # Pharmacy → Supplier payment
    pharmacy_payment_status = db.Column(db.String(20), default='pending')
    pharmacy_payment_id = db.Column(db.String(100))

    # Supplier → Manufacturer payment
    supplier_payment_status = db.Column(db.String(20), default='pending')
    supplier_payment_id = db.Column(db.String(100))

    # Workflow status
    status = db.Column(db.String(40), default='pending_supplier')
    # Flow:
    # pending_supplier
    # -> supplier_accepted
    # -> pharmacy_paid
    # -> supplier_ordered_manufacturer
    # -> supplier_paid_manufacturer
    # -> manufacturer_processing
    # -> manufacturer_packed
    # -> manufacturer_dispatched
    # -> shipped
    # -> delivered

    notes = db.Column(db.Text)
    packing_notes = db.Column(db.Text)
    dispatch_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    tracking_number = db.Column(db.String(80))
    manufacturer_processed_at = db.Column(db.DateTime)
    packed_at = db.Column(db.DateTime)
    dispatched_at = db.Column(db.DateTime)
    delivery_date = db.Column(db.DateTime)

    pharmacy = db.relationship('User', foreign_keys=[pharmacy_id])
    supplier = db.relationship('User', foreign_keys=[supplier_id])
    manufacturer = db.relationship('User', foreign_keys=[manufacturer_id])
    medicine = db.relationship('MedicineProduct', foreign_keys=[medicine_id])

class MedicineCertificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    manufacturer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    batch_number = db.Column(db.String(50), nullable=False)
    certificate_file = db.Column(db.String(500))
    certificate_filename = db.Column(db.String(200))
    quality_grade = db.Column(db.String(20))
    test_results = db.Column(db.Text)
    issue_date = db.Column(db.DateTime, default=datetime.utcnow)
    expiry_date = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='pending')
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    manufacturer = db.relationship('User', foreign_keys=[manufacturer_id])
    approver = db.relationship('User', foreign_keys=[approved_by])


# class PharmacyRequest(db.Model):
#     """Pharmacy -> Supplier -> Manufacturer order workflow"""
#     id = db.Column(db.Integer, primary_key=True)
#     pharmacy_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
#     supplier_id = db.Column(db.Integer, db.ForeignKey('user.id'))
#     medicine_id = db.Column(db.Integer, db.ForeignKey('medicine_product.id'), nullable=False)
#     medicine_name = db.Column(db.String(150))
#     quantity = db.Column(db.Float, nullable=False)
#     unit = db.Column(db.String(20), default='strip')
#     expected_price = db.Column(db.Float)
#     final_price = db.Column(db.Float)

#     # Status flow:
#     # pending_supplier -> supplier_checking_manufacturer -> manufacturer_confirmed ->
#     # supplier_approved -> pharmacy_paid -> supplier_paid_manufacturer ->
#     # manufacturer_processing -> manufacturer_packed -> manufacturer_dispatched ->
#     # shipped -> delivered -> cancelled
#     status = db.Column(db.String(40), default='pending_supplier')
#     notes = db.Column(db.Text)
#     packing_notes = db.Column(db.Text)     # NEW: manufacturer packing notes
#     dispatch_notes = db.Column(db.Text)    # NEW: dispatch info
#     created_at = db.Column(db.DateTime, default=datetime.utcnow)
#     updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
#     tracking_number = db.Column(db.String(80))
#     pharmacy_payment_id = db.Column(db.String(100))
#     supplier_payment_id = db.Column(db.String(100))
#     manufacturer_processed_at = db.Column(db.DateTime)
#     packed_at = db.Column(db.DateTime)
#     dispatched_at = db.Column(db.DateTime)
#     delivery_date = db.Column(db.DateTime)

#     pharmacy = db.relationship('User', foreign_keys=[pharmacy_id])
#     supplier = db.relationship('User', foreign_keys=[supplier_id])
#     medicine = db.relationship('MedicineProduct', foreign_keys=[medicine_id])


class UserOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='strip')
    price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='pending')
    payment_status = db.Column(db.String(20), default='pending')
    receipt_number = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])
    pharmacy = db.relationship('User', foreign_keys=[pharmacy_id])


class PaymentReceipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(100), unique=True, nullable=False)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    purpose = db.Column(db.String(100))
    reference_id = db.Column(db.Integer)
    reference_type = db.Column(db.String(50))
    status = db.Column(db.String(20), default='completed')
    blockchain_hash = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    from_user = db.relationship('User', foreign_keys=[from_user_id])
    to_user = db.relationship('User', foreign_keys=[to_user_id])


class MedicineReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('medicine_product.id'), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    review_text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reviewer = db.relationship('User', foreign_keys=[reviewer_id])


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(50))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    link = db.Column(db.String(200))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ==================== DECORATORS ====================

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in allowed_roles:
                flash('Access denied.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ==================== HELPERS ====================

def create_notification(user_id, title, message, type='general', link=None):
    n = Notification(user_id=user_id, title=title, message=message, type=type, link=link)
    db.session.add(n)
    db.session.commit()


def log_to_blockchain(event_type, event_data):
    try:
        bc = get_blockchain()
        return bc.add_event(event_type, event_data)
    except Exception as e:
        print(f"Blockchain error: {e}")
        return None


def generate_receipt_number():
    return f"RCPT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def create_receipt(from_user_id, to_user_id, amount, purpose, reference_id, reference_type, notes=''):
    receipt = PaymentReceipt(
        receipt_number=generate_receipt_number(),
        from_user_id=from_user_id, to_user_id=to_user_id,
        amount=amount, purpose=purpose,
        reference_id=reference_id, reference_type=reference_type,
        notes=notes
    )
    db.session.add(receipt)
    db.session.commit()
    return receipt


# ==================== AUTH ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.is_approved:
                flash('Your account has been suspended by admin.', 'danger')
                return redirect(url_for('login'))
            login_user(user)
            session['user_role'] = user.role
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template('auth/login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')
        license_number = (request.form.get('license_number') or '').strip().upper()
        company_name = request.form.get('company_name', '')
        phone = request.form.get('phone', '')
        address = request.form.get('address', '')

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))

        # ✅ License validation for manufacturer/supplier/pharmacy
        if role in ['manufacturer', 'supplier', 'pharmacy']:
            if not license_number:
                flash('License number is required for this role.', 'danger')
                return redirect(url_for('register'))

            is_valid, result = validate_license(license_number, role)
            if not is_valid:
                # 🚨 FAKE LICENSE ALERT
                log_to_blockchain('FAKE_LICENSE_ATTEMPT', {
                    'attempted_license': license_number,
                    'attempted_role': role,
                    'username': username,
                    'email': email,
                    'reason': result,
                    'timestamp': datetime.utcnow().isoformat()
                })
                flash(f'❌ REGISTRATION REJECTED: {result}', 'danger')
                return redirect(url_for('register'))

            # Check license not already used
            if User.query.filter_by(license_number=license_number).first():
                flash(f'❌ License {license_number} is already registered.', 'danger')
                return redirect(url_for('register'))

            # Auto-fill company from license if empty
            if not company_name:
                company_name = result['company']

        user = User(
            username=username, email=email, role=role,
            license_number=license_number if license_number else None,
            company_name=company_name, phone=phone, address=address
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        log_to_blockchain('USER_REGISTERED', {
            'username': username, 'role': role,
            'license': license_number, 'company': company_name,
            'license_verified': role in ['manufacturer', 'supplier', 'pharmacy'],
            'timestamp': datetime.utcnow().isoformat()
        })

        flash('✅ Registration successful! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('auth/register.html', valid_licenses=VALID_LICENSES)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    r = current_user.role
    if r == 'admin':
        return redirect(url_for('admin_dashboard'))
    if r == 'manufacturer':
        return redirect(url_for('manufacturer_dashboard'))
    if r == 'supplier':
        return redirect(url_for('supplier_dashboard'))
    if r == 'pharmacy':
        return redirect(url_for('pharmacy_dashboard'))
    if r == 'user':
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('index'))


@app.route('/notifications')
@login_required
def view_notifications():
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    for n in notifications:
        n.read = True
    db.session.commit()
    return render_template('notifications.html', notifications=notifications)


@app.route('/api/notifications/unread')
@login_required
def unread_notifications():
    count = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    return jsonify({'count': count})


@app.route('/certificate/download/<int:cert_id>')
@login_required
def download_certificate(cert_id):
    """Download a certificate file"""
    cert = MedicineCertificate.query.get_or_404(cert_id)

    # Permission check: admin or the manufacturer who uploaded it
    if current_user.role != 'admin' and cert.manufacturer_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))

    if not cert.certificate_file:
        flash('No certificate file found.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    # Extract filename from stored URL like '/static/uploads/certificates/cert_xxx.pdf'
    filename = cert.certificate_file.split('/')[-1]
    cert_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'certificates')

    return send_from_directory(
        cert_folder,
        filename,
        as_attachment=True,
        download_name=cert.certificate_filename or 'certificate'
    )


@app.route('/certificate/view/<int:cert_id>')
@login_required
def view_certificate_file(cert_id):
    """View certificate inline (for PDF/image)"""
    cert = MedicineCertificate.query.get_or_404(cert_id)

    if current_user.role != 'admin' and cert.manufacturer_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))

    if not cert.certificate_file:
        flash('No certificate file found.', 'danger')
        return redirect(request.referrer or url_for('dashboard'))

    filename = cert.certificate_file.split('/')[-1]
    cert_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'certificates')

    return send_from_directory(cert_folder, filename, as_attachment=False)
# ==================== ADMIN ROUTES ====================

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    stats = {
        'total_manufacturers': User.query.filter_by(role='manufacturer').count(),
        'total_suppliers': User.query.filter_by(role='supplier').count(),
        'total_pharmacies': User.query.filter_by(role='pharmacy').count(),
        'total_users': User.query.filter_by(role='user').count(),
        'total_products': MedicineProduct.query.count(),
        'total_requests': PharmacyRequest.query.count(),
        'pending_certificates': MedicineCertificate.query.filter_by(status='pending').count(),
        'approved_certificates': MedicineCertificate.query.filter_by(status='approved').count(),
        'rejected_certificates': MedicineCertificate.query.filter_by(status='rejected').count(),
        'total_certificates': MedicineCertificate.query.count(),
        'total_receipts': PaymentReceipt.query.count(),
        'fake_license_attempts': len([b for b in get_blockchain().get_all_blocks() if b.get('event_type') == 'FAKE_LICENSE_ATTEMPT'])
    }
    pending_cert_list = MedicineCertificate.query.filter_by(status='pending').limit(5).all()
    recent_requests = PharmacyRequest.query.order_by(PharmacyRequest.created_at.desc()).limit(5).all()
    return render_template('admin/dashboard.html', stats=stats,
                           pending_cert_list=pending_cert_list,
                           recent_requests=recent_requests)


@app.route('/admin/manage-certificates')
@login_required
@admin_required
def manage_certificates():
    certificates = MedicineCertificate.query.order_by(MedicineCertificate.created_at.desc()).all()
    return render_template('admin/manage_certificates.html', certificates=certificates)


@app.route('/admin/view-certificate/<int:cert_id>')
@login_required
@admin_required
def admin_view_certificate(cert_id):
    cert = MedicineCertificate.query.get_or_404(cert_id)
    return render_template('admin/view_certificate.html', cert=cert)


@app.route('/admin/approve-certificate/<int:cert_id>')
@login_required
@admin_required
def approve_certificate(cert_id):
    cert = MedicineCertificate.query.get_or_404(cert_id)
    cert.status = 'approved'
    cert.approved_by = current_user.id
    cert.approved_at = datetime.utcnow()

    products = MedicineProduct.query.filter_by(certificate_id=cert.id).all()
    for p in products:
        p.status = 'available'

    log_to_blockchain('CERTIFICATE_APPROVED', {
        'certificate_id': cert.id, 'medicine': cert.product_name,
        'batch': cert.batch_number,
        'manufacturer': User.query.get(cert.manufacturer_id).username,
        'admin': current_user.username,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(cert.manufacturer_id, 'Certificate Approved',
                        f'Your certificate for {cert.product_name} has been approved.',
                        'certificate', url_for('manufacturer_dashboard'))
    db.session.commit()
    flash('Certificate approved!', 'success')
    return redirect(url_for('manage_certificates'))


@app.route('/admin/reject-certificate/<int:cert_id>', methods=['POST'])
@login_required
@admin_required
def reject_certificate(cert_id):
    cert = MedicineCertificate.query.get_or_404(cert_id)
    reason = request.form.get('reason', 'No reason provided')
    cert.status = 'rejected'
    cert.rejection_reason = reason

    products = MedicineProduct.query.filter_by(certificate_id=cert.id).all()
    for p in products:
        p.status = 'rejected'

    log_to_blockchain('CERTIFICATE_REJECTED', {
        'certificate_id': cert.id, 'medicine': cert.product_name,
        'reason': reason, 'admin': current_user.username,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(cert.manufacturer_id, 'Certificate Rejected',
                        f'Your certificate for {cert.product_name} was rejected. Reason: {reason}',
                        'certificate', url_for('manufacturer_dashboard'))
    db.session.commit()
    flash('Certificate rejected.', 'warning')
    return redirect(url_for('manage_certificates'))


@app.route('/admin/manage-users')
@login_required
@admin_required
def manage_users():
    users = User.query.filter(User.role != 'admin').order_by(User.created_at.desc()).all()
    return render_template('admin/manage_users.html', users=users)


@app.route('/admin/toggle-user/<int:user_id>')
@login_required
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_approved = not user.is_approved
    db.session.commit()
    flash(f'User {user.username} {"activated" if user.is_approved else "suspended"}.', 'success')
    return redirect(url_for('manage_users'))


@app.route('/admin/receipts')
@login_required
@admin_required
def admin_receipts():
    receipts = PaymentReceipt.query.order_by(PaymentReceipt.created_at.desc()).all()
    return render_template('admin/receipts.html', receipts=receipts)


@app.route('/admin/licenses')
@login_required
@admin_required
def admin_licenses():
    """View all predefined licenses"""
    return render_template('admin/licenses.html', licenses=VALID_LICENSES)


# ==================== MANUFACTURER ROUTES ====================

@app.route('/manufacturer/dashboard')
@login_required
@role_required(['manufacturer'])
def manufacturer_dashboard():
    products = MedicineProduct.query.filter_by(manufacturer_id=current_user.id).order_by(MedicineProduct.created_at.desc()).all()
    certificates = MedicineCertificate.query.filter_by(manufacturer_id=current_user.id).order_by(MedicineCertificate.created_at.desc()).all()
    notifications = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    receipts = PaymentReceipt.query.filter_by(to_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).limit(10).all()

    # Orders directed at this manufacturer
    incoming_orders = PharmacyRequest.query.filter(
        PharmacyRequest.manufacturer_id == current_user.id,
        PharmacyRequest.status.in_([
            'supplier_ordered_manufacturer',
            'supplier_paid_manufacturer',
            'manufacturer_processing',
            'manufacturer_packed',
            'manufacturer_dispatched',
            'shipped',
            'delivered'
        ])
    ).order_by(PharmacyRequest.created_at.desc()).all()

    pending_orders = [o for o in incoming_orders if o.status in ['supplier_ordered_manufacturer', 'supplier_paid_manufacturer']]
    processing_orders = [o for o in incoming_orders if o.status == 'manufacturer_processing']
    packed_orders = [o for o in incoming_orders if o.status == 'manufacturer_packed']

    total_revenue = sum(r.amount for r in PaymentReceipt.query.filter_by(to_user_id=current_user.id).all())

    return render_template('manufacturer/dashboard.html',
                           products=products, certificates=certificates,
                           notifications=notifications, receipts=receipts,
                           total_revenue=total_revenue,
                           total_products=len(products),
                           pending_certs=len([c for c in certificates if c.status == 'pending']),
                           approved_certs=len([c for c in certificates if c.status == 'approved']),
                           incoming_orders=incoming_orders,
                           pending_orders=pending_orders,
                           processing_orders=processing_orders,
                           packed_orders=packed_orders)



@app.route('/manufacturer/add-medicine', methods=['GET', 'POST'])
@login_required
@role_required(['manufacturer'])
def add_medicine():
    if request.method == 'POST':
        name = request.form.get('name')
        composition = request.form.get('composition')
        category = request.form.get('category')
        batch_number = request.form.get('batch_number')
        mfg_date = datetime.strptime(request.form.get('mfg_date'), '%Y-%m-%d')
        expiry_date = datetime.strptime(request.form.get('expiry_date'), '%Y-%m-%d')
        quantity = float(request.form.get('quantity'))
        unit = request.form.get('unit')
        price_per_unit = float(request.form.get('price_per_unit'))
        description = request.form.get('description')
        uses = request.form.get('uses', '')
        side_effects = request.form.get('side_effects', '')
        dosage = request.form.get('dosage', '')
        storage = request.form.get('storage', '')
        prescription_required = request.form.get('prescription_required') == 'on'
        quality_grade = request.form.get('quality_grade')
        test_results = request.form.get('test_results')

        image_filename = image_url = None
        if 'product_image' in request.files:
            file = request.files['product_image']
            if file and file.filename and allowed_file(file.filename):
                original = secure_filename(file.filename)
                unique = f"{uuid.uuid4().hex}_{original}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique))
                image_filename = original
                image_url = f'/static/uploads/{unique}'

        certificate_file = certificate_filename = None
        if 'certificate_file' in request.files:
            file = request.files['certificate_file']
            if file and file.filename and allowed_certificate(file.filename):
                original = secure_filename(file.filename)
                unique = f"cert_{uuid.uuid4().hex}_{original}"
                cert_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'certificates')
                file.save(os.path.join(cert_folder, unique))
                certificate_filename = original
                certificate_file = f'/static/uploads/certificates/{unique}'

        cert = MedicineCertificate(
            manufacturer_id=current_user.id, product_name=name,
            batch_number=batch_number, quality_grade=quality_grade,
            test_results=test_results, expiry_date=expiry_date,
            status='pending', certificate_file=certificate_file,
            certificate_filename=certificate_filename
        )
        db.session.add(cert)
        db.session.commit()

        product = MedicineProduct(
            manufacturer_id=current_user.id, name=name, composition=composition,
            category=category, batch_number=batch_number,
            mfg_date=mfg_date, expiry_date=expiry_date,
            quantity=quantity, unit=unit, price_per_unit=price_per_unit,
            description=description, uses=uses, side_effects=side_effects,
            dosage=dosage, storage=storage,
            prescription_required=prescription_required,
            image_filename=image_filename, image_url=image_url,
            certificate_id=cert.id, status='pending'
        )
        db.session.add(product)
        db.session.commit()

        log_to_blockchain('MEDICINE_ADDED', {
            'product_id': product.id, 'certificate_id': cert.id,
            'medicine': name, 'batch': batch_number,
            'manufacturer': current_user.username,
            'manufacturer_license': current_user.license_number,
            'timestamp': datetime.utcnow().isoformat()
        })

        for admin in User.query.filter_by(role='admin').all():
            create_notification(admin.id, 'New Medicine Certificate for Approval',
                                f'{current_user.username} added {name}.',
                                'certificate', url_for('manage_certificates'))

        flash('Medicine added! Certificate submitted for admin approval.', 'success')
        return redirect(url_for('manufacturer_dashboard'))
    return render_template('manufacturer/add_medicine.html')


@app.route('/manufacturer/delete-medicine/<int:product_id>')
@login_required
@role_required(['manufacturer'])
def delete_medicine(product_id):
    p = MedicineProduct.query.get_or_404(product_id)
    if p.manufacturer_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('manufacturer_dashboard'))
    if p.image_url:
        path = os.path.join(app.config['UPLOAD_FOLDER'], p.image_url.replace('/static/uploads/', ''))
        if os.path.exists(path):
            os.remove(path)
    db.session.delete(p)
    db.session.commit()
    flash('Medicine deleted.', 'success')
    return redirect(url_for('manufacturer_dashboard'))


# ✅ NEW: Manufacturer processes order
@app.route('/manufacturer/process-order/<int:req_id>', methods=['GET', 'POST'])
@login_required
@role_required(['manufacturer'])
def manufacturer_process_order(req_id):
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.manufacturer_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('manufacturer_dashboard'))

    if req.status not in ['supplier_ordered_manufacturer', 'supplier_paid_manufacturer']:
        flash(f'Order is in status "{req.status}" — cannot process.', 'warning')
        return redirect(url_for('manufacturer_dashboard'))

    if request.method == 'POST':
        packing_notes = request.form.get('packing_notes', '')
        req.packing_notes = packing_notes
        req.status = 'manufacturer_processing'
        req.manufacturer_processed_at = datetime.utcnow()

        log_to_blockchain('MANUFACTURER_PROCESSING', {
            'request_id': req.id, 'medicine': req.medicine_name,
            'manufacturer': current_user.username,
            'packing_notes': packing_notes,
            'timestamp': datetime.utcnow().isoformat()
        })

        create_notification(req.supplier_id, 'Manufacturer Processing Order',
                            f'Manufacturer is preparing {req.medicine_name}.',
                            'order', url_for('supplier_dashboard'))
        db.session.commit()
        flash('Order processing started!', 'success')
        return redirect(url_for('manufacturer_dashboard'))

    return render_template('manufacturer/process_order.html', req=req, medicine=req.medicine)




@app.route('/manufacturer/pack-order/<int:req_id>', methods=['POST'])
@login_required
@role_required(['manufacturer'])
def manufacturer_pack_order(req_id):
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.manufacturer_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('manufacturer_dashboard'))

    if req.status != 'manufacturer_processing':
        flash('Order must be in processing state.', 'warning')
        return redirect(url_for('manufacturer_dashboard'))

    req.status = 'manufacturer_packed'
    req.packed_at = datetime.utcnow()

    log_to_blockchain('MANUFACTURER_PACKED', {
        'request_id': req.id, 'medicine': req.medicine_name,
        'manufacturer': current_user.username,
        'timestamp': datetime.utcnow().isoformat()
    })
    db.session.commit()
    flash('Order packed! Ready to dispatch.', 'success')
    return redirect(url_for('manufacturer_dashboard'))



@app.route('/manufacturer/dispatch-order/<int:req_id>', methods=['POST'])
@login_required
@role_required(['manufacturer'])
def manufacturer_dispatch_order(req_id):
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.manufacturer_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('manufacturer_dashboard'))

    if req.status != 'manufacturer_packed':
        flash('Order must be packed first.', 'warning')
        return redirect(url_for('manufacturer_dashboard'))

    dispatch_notes = request.form.get('dispatch_notes', '')
    req.dispatch_notes = dispatch_notes
    req.status = 'manufacturer_dispatched'
    req.dispatched_at = datetime.utcnow()

    # Mark medicine as dispatched globally
    med = req.medicine
    if med:
        med.is_dispatched = True
        med.dispatched_at = datetime.utcnow()
        if med.quantity >= req.quantity:
            med.quantity -= req.quantity
        if med.quantity <= 0:
            med.status = 'out_of_stock'

    log_to_blockchain('MANUFACTURER_DISPATCHED', {
        'request_id': req.id, 'medicine': req.medicine_name,
        'manufacturer': current_user.username,
        'dispatch_notes': dispatch_notes,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(req.supplier_id, 'Manufacturer Dispatched Order',
                        f'{req.medicine_name} dispatched. Ready to ship to pharmacy.',
                        'shipping', url_for('supplier_dashboard'))

    db.session.commit()
    flash('Order dispatched! Supplier notified.', 'success')
    return redirect(url_for('manufacturer_dashboard'))


@app.route('/manufacturer/receipts')
@login_required
@role_required(['manufacturer'])
def manufacturer_receipts():
    receipts = PaymentReceipt.query.filter_by(to_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).all()
    return render_template('manufacturer/receipts.html', receipts=receipts)


# ==================== SUPPLIER ROUTES ====================
# ==================== SUPPLIER ROUTES ====================

@app.route('/supplier/dashboard')
@login_required
@role_required(['supplier'])
def supplier_dashboard():
    requests = PharmacyRequest.query.filter_by(supplier_id=current_user.id).order_by(PharmacyRequest.created_at.desc()).all()
    unassigned = PharmacyRequest.query.filter_by(supplier_id=None, status='pending_supplier').all()
    notifications = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    receipts = PaymentReceipt.query.filter_by(from_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).limit(10).all()

    total_earnings = sum(r.amount for r in PaymentReceipt.query.filter_by(to_user_id=current_user.id).all())

    # Requests needing action from supplier
    ready_to_order_mfg = [r for r in requests if r.status == 'pharmacy_paid']    # pay mfg + send order
    ready_to_pay_mfg = [r for r in requests if r.status == 'supplier_ordered_manufacturer']  # pay mfg
    ready_to_ship = [r for r in requests if r.status == 'manufacturer_dispatched']  # ship to pharmacy
    shipped = [r for r in requests if r.status == 'shipped']

    return render_template('supplier/dashboard.html',
                           requests=requests, unassigned=unassigned,
                           ready_to_order_mfg=ready_to_order_mfg,
                           ready_to_pay_mfg=ready_to_pay_mfg,
                           ready_to_ship=ready_to_ship,
                           shipped=shipped,
                           receipts=receipts,
                           total_earnings=total_earnings,
                           notifications=notifications)


@app.route('/supplier/accept-request/<int:req_id>')
@login_required
@role_required(['supplier'])
def supplier_accept_request(req_id):
    """Supplier accepts pharmacy request, sets a final price"""
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.supplier_id and req.supplier_id != current_user.id:
        flash('Already assigned to another supplier.', 'warning')
        return redirect(url_for('supplier_dashboard'))

    # Fetch medicine + set final price
    med = MedicineProduct.query.get(req.medicine_id)
    if not med:
        flash('Medicine no longer available.', 'danger')
        return redirect(url_for('supplier_dashboard'))

    # Supplier sets price: retail price from medicine + supplier markup
    # Example: supplier charges pharmacy retail price (med.price_per_unit * qty)
    final_price = med.price_per_unit * req.quantity
    req.final_price = final_price
    req.supplier_id = current_user.id
    req.manufacturer_id = med.manufacturer_id
    req.status = 'supplier_accepted'
    db.session.commit()

    log_to_blockchain('SUPPLIER_ACCEPTED_REQUEST', {
        'request_id': req.id,
        'pharmacy': req.pharmacy.username,
        'supplier': current_user.username,
        'manufacturer': med.manufacturer.username,
        'medicine': req.medicine_name,
        'final_price': final_price,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(req.pharmacy_id, 'Supplier Accepted - Pay Now',
                        f'{current_user.username} accepted your request. '
                        f'Amount: ${final_price:.2f}',
                        'payment', url_for('pharmacy_dashboard'))

    flash(f'Accepted! Pharmacy notified to pay ${final_price:.2f}.', 'success')
    return redirect(url_for('supplier_dashboard'))


@app.route('/supplier/send-order-to-manufacturer/<int:req_id>')
@login_required
@role_required(['supplier'])
def supplier_send_order_to_manufacturer(req_id):
    """After pharmacy pays, supplier sends order to manufacturer"""
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.supplier_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('supplier_dashboard'))

    if req.status != 'pharmacy_paid':
        flash('Pharmacy must pay first.', 'warning')
        return redirect(url_for('supplier_dashboard'))

    med = MedicineProduct.query.get(req.medicine_id)
    manufacturer = User.query.get(med.manufacturer_id)

    req.manufacturer_id = manufacturer.id
    req.status = 'supplier_ordered_manufacturer'
    db.session.commit()

    log_to_blockchain('SUPPLIER_SENT_ORDER_TO_MANUFACTURER', {
        'request_id': req.id,
        'supplier': current_user.username,
        'manufacturer': manufacturer.username,
        'medicine': req.medicine_name,
        'quantity': req.quantity,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(manufacturer.id, 'New Order from Supplier',
                        f'{current_user.username} needs {req.quantity} of {req.medicine_name}. '
                        f'Please confirm and prepare for dispatch.',
                        'order', url_for('manufacturer_dashboard'))

    flash(f'Order sent to {manufacturer.company_name or manufacturer.username}.', 'success')
    return redirect(url_for('supplier_dashboard'))


@app.route('/supplier/pay-manufacturer/<int:req_id>')
@login_required
@role_required(['supplier'])
def supplier_pay_manufacturer(req_id):
    """Supplier pays manufacturer for the medicine (wholesale price)"""
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.supplier_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('supplier_dashboard'))

    if req.status not in ['pharmacy_paid', 'supplier_ordered_manufacturer']:
        flash('Cannot pay in current status.', 'warning')
        return redirect(url_for('supplier_dashboard'))

    med = MedicineProduct.query.get(req.medicine_id)
    manufacturer = User.query.get(req.manufacturer_id or med.manufacturer_id)

    # Wholesale price = 70% of pharmacy amount (30% supplier margin)
    wholesale_amount = (req.final_price or (med.price_per_unit * req.quantity)) * 0.7

    if current_user.wallet_balance < wholesale_amount:
        flash('Insufficient wallet balance.', 'danger')
        return redirect(url_for('supplier_dashboard'))

    # Transfer
    current_user.wallet_balance -= wholesale_amount
    manufacturer.wallet_balance = (manufacturer.wallet_balance or 0) + wholesale_amount

    # Receipt
    receipt = create_receipt(
        from_user_id=current_user.id, to_user_id=manufacturer.id,
        amount=wholesale_amount, purpose='supplier_to_manufacturer',
        reference_id=req.id, reference_type='pharmacy_request',
        notes=f'Wholesale payment for {req.medicine_name}'
    )

    req.supplier_payment_id = receipt.receipt_number
    req.supplier_payment_status = 'paid'
    req.status = 'supplier_paid_manufacturer'
    db.session.commit()

    log_to_blockchain('SUPPLIER_PAID_MANUFACTURER', {
        'request_id': req.id,
        'supplier': current_user.username,
        'manufacturer': manufacturer.username,
        'amount': wholesale_amount,
        'receipt': receipt.receipt_number,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(manufacturer.id, 'Payment Received - Process Order',
                        f'{current_user.username} paid ${wholesale_amount:.2f} for {req.medicine_name}. '
                        f'Please process, pack and dispatch.',
                        'payment', url_for('manufacturer_dashboard'))

    flash(f'Paid ${wholesale_amount:.2f} to manufacturer. Receipt: {receipt.receipt_number}', 'success')
    return redirect(url_for('supplier_dashboard'))


@app.route('/supplier/ship-request/<int:req_id>')
@login_required
@role_required(['supplier'])
def supplier_ship_request(req_id):
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.supplier_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('supplier_dashboard'))

    if req.status != 'manufacturer_dispatched':
        flash('Manufacturer must dispatch the order first.', 'warning')
        return redirect(url_for('supplier_dashboard'))

    req.status = 'shipped'
    req.tracking_number = f'MED-{req.id}-{datetime.utcnow().strftime("%Y%m%d%H%M")}'

    log_to_blockchain('ORDER_SHIPPED', {
        'request_id': req.id, 'medicine': req.medicine_name,
        'supplier': current_user.username, 'tracking': req.tracking_number,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(req.pharmacy_id, 'Order Shipped',
                        f'Tracking: {req.tracking_number}',
                        'shipping', url_for('pharmacy_dashboard'))
    db.session.commit()
    flash('Order shipped!', 'success')
    return redirect(url_for('supplier_dashboard'))


@app.route('/supplier/deliver-request/<int:req_id>')
@login_required
@role_required(['supplier'])
def supplier_deliver_request(req_id):
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.supplier_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('supplier_dashboard'))
    req.status = 'delivered'
    req.delivery_date = datetime.utcnow()

    log_to_blockchain('ORDER_DELIVERED', {
        'request_id': req.id, 'medicine': req.medicine_name,
        'supplier': current_user.username,
        'pharmacy': req.pharmacy.username,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(req.pharmacy_id, 'Order Delivered',
                        f'{req.medicine_name} delivered!',
                        'order', url_for('pharmacy_dashboard'))
    db.session.commit()
    flash('Delivered!', 'success')
    return redirect(url_for('supplier_dashboard'))


@app.route('/supplier/receipts')
@login_required
@role_required(['supplier'])
def supplier_receipts():
    sent = PaymentReceipt.query.filter_by(from_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).all()
    received = PaymentReceipt.query.filter_by(to_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).all()
    return render_template('supplier/receipts.html', sent=sent, received=received)


# ==================== PHARMACY ROUTES ====================
# ==================== PHARMACY ROUTES ====================
@app.route('/pharmacy/dashboard')
@login_required
@role_required(['pharmacy'])
def pharmacy_dashboard():
    requests = PharmacyRequest.query.filter_by(pharmacy_id=current_user.id).order_by(PharmacyRequest.created_at.desc()).all()
    notifications = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    receipts = PaymentReceipt.query.filter_by(from_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).limit(10).all()

    # Show all orderable medicines
    available_medicines = MedicineProduct.query.filter(
        MedicineProduct.status.in_(['available', 'dispatched', 'out_of_stock'])
    ).all()

    user_orders = UserOrder.query.filter_by(pharmacy_id=current_user.id).order_by(UserOrder.created_at.desc()).all()
    pending_payment = [r for r in requests if r.status == 'supplier_accepted']

    return render_template('pharmacy/dashboard.html',
                           requests=requests, receipts=receipts,
                           available_medicines=available_medicines,
                           user_orders=user_orders,
                           pending_payment=pending_payment,
                           notifications=notifications,
                           total_requests=len(requests),
                           pending_count=len([r for r in requests if r.status not in ['delivered', 'cancelled']]),
                           delivered_count=len([r for r in requests if r.status == 'delivered']))
# @app.route('/pharmacy/dispatched-medicines')
# @login_required
# @role_required(['pharmacy'])
# def pharmacy_dispatched_medicines():
#     medicines = MedicineProduct.query.filter_by(is_dispatched=True).order_by(MedicineProduct.dispatched_at.desc()).all()
#     return render_template('pharmacy/dispatched_medicines.html', medicines=medicines)

@app.route('/pharmacy/create-request', methods=['GET', 'POST'])
@login_required
@role_required(['pharmacy'])
def create_request():
    if request.method == 'POST':
        medicine_id = request.form.get('medicine_id')
        quantity = float(request.form.get('quantity'))
        notes = request.form.get('notes', '')

        med = MedicineProduct.query.get_or_404(medicine_id)

        # ✅ Accept any medicine that is available OR dispatched OR out_of_stock
        if med.status not in ['available', 'dispatched', 'out_of_stock']:
            flash(f'Medicine "{med.name}" is not available for ordering (status: {med.status}).', 'danger')
            return redirect(url_for('create_request'))

        if med.quantity < quantity:
            flash(f'Only {med.quantity} units available.', 'danger')
            return redirect(url_for('create_request'))

        req = PharmacyRequest(
            pharmacy_id=current_user.id, medicine_id=med.id,
            medicine_name=med.name, quantity=quantity, unit=med.unit,
            expected_price=med.price_per_unit * quantity,
            manufacturer_id=med.manufacturer_id,
            status='pending_supplier', notes=notes
        )
        db.session.add(req)
        db.session.commit()

        log_to_blockchain('PHARMACY_REQUEST_CREATED', {
            'request_id': req.id,
            'pharmacy': current_user.username,
            'pharmacy_license': current_user.license_number,
            'medicine': med.name,
            'quantity': quantity,
            'manufacturer': med.manufacturer.username if med.manufacturer else 'N/A',
            'timestamp': datetime.utcnow().isoformat()
        })

        # Notify all suppliers
        for s in User.query.filter_by(role='supplier').all():
            create_notification(
                s.id, 'New Pharmacy Request',
                f'{current_user.username} needs {quantity} {med.unit} of {med.name}.',
                'order', url_for('supplier_dashboard')
            )

        flash('✅ Request sent to all suppliers!', 'success')
        return redirect(url_for('pharmacy_dashboard'))

    # ✅ SHOW all orderable medicines (available OR dispatched)
    medicines = MedicineProduct.query.filter(
        MedicineProduct.status.in_(['available', 'dispatched', 'out_of_stock'])
    ).order_by(MedicineProduct.name).all()

    return render_template('pharmacy/create_request.html', medicines=medicines)
@app.route('/pharmacy/available-medicines')
@login_required
@role_required(['pharmacy'])
def pharmacy_available_medicines():
    """List of medicines that pharmacy can order (no quantities shown)"""
    medicines = MedicineProduct.query.filter(
        MedicineProduct.status.in_(['available', 'dispatched', 'out_of_stock'])
    ).order_by(MedicineProduct.name).all()
    return render_template('pharmacy/available_medicines.html', medicines=medicines)


# Keep old route as alias
@app.route('/pharmacy/dispatched-medicines')
@login_required
@role_required(['pharmacy'])
def pharmacy_dispatched_medicines():
    return redirect(url_for('pharmacy_available_medicines'))

@app.route('/pharmacy/pay-supplier/<int:req_id>')
@login_required
@role_required(['pharmacy'])
def pharmacy_pay_supplier(req_id):
    req = PharmacyRequest.query.get_or_404(req_id)
    if req.pharmacy_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('pharmacy_dashboard'))

    if req.status != 'supplier_accepted':
        flash('Supplier has not accepted yet.', 'warning')
        return redirect(url_for('pharmacy_dashboard'))

    amount = req.final_price
    if current_user.wallet_balance < amount:
        flash('Insufficient wallet balance.', 'danger')
        return redirect(url_for('pharmacy_dashboard'))

    # Transfer pharmacy → supplier
    current_user.wallet_balance -= amount
    supplier = User.query.get(req.supplier_id)
    supplier.wallet_balance = (supplier.wallet_balance or 0) + amount

    receipt = create_receipt(
        from_user_id=current_user.id, to_user_id=supplier.id,
        amount=amount, purpose='pharmacy_to_supplier',
        reference_id=req.id, reference_type='pharmacy_request',
        notes=f'Payment to supplier for {req.medicine_name}'
    )

    req.pharmacy_payment_id = receipt.receipt_number
    req.pharmacy_payment_status = 'paid'
    req.status = 'pharmacy_paid'

    log_to_blockchain('PHARMACY_PAID_SUPPLIER', {
        'request_id': req.id, 'pharmacy': current_user.username,
        'supplier': supplier.username, 'amount': amount,
        'receipt': receipt.receipt_number,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(supplier.id, 'Payment Received from Pharmacy',
                        f'{current_user.username} paid ${amount:.2f}. '
                        f'Now send order to manufacturer.',
                        'payment', url_for('supplier_dashboard'))

    db.session.commit()
    flash(f'Paid ${amount:.2f} to supplier. Receipt: {receipt.receipt_number}', 'success')
    return redirect(url_for('pharmacy_dashboard'))


@app.route('/pharmacy/receipts')
@login_required
@role_required(['pharmacy'])
def pharmacy_receipts():
    sent = PaymentReceipt.query.filter_by(from_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).all()
    received = PaymentReceipt.query.filter_by(to_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).all()
    return render_template('pharmacy/receipts.html', sent=sent, received=received)


@app.route('/pharmacy/sell-to-user/<int:order_id>')
@login_required
@role_required(['pharmacy'])
def pharmacy_sell_to_user(order_id):
    order = UserOrder.query.get_or_404(order_id)
    if order.pharmacy_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('pharmacy_dashboard'))
    order.status = 'ready'
    db.session.commit()
    create_notification(order.user_id, 'Medicine Ready',
                        f'Ready at {current_user.company_name}.',
                        'order', url_for('user_dashboard'))
    flash('Marked ready.', 'success')
    return redirect(url_for('pharmacy_dashboard'))

# ==================== USER ROUTES ====================

@app.route('/user/dashboard')
@login_required
@role_required(['user'])
def user_dashboard():
    orders = UserOrder.query.filter_by(user_id=current_user.id).order_by(UserOrder.created_at.desc()).all()
    pharmacies = User.query.filter_by(role='pharmacy').all()
    notifications = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    receipts = PaymentReceipt.query.filter_by(from_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).limit(10).all()
    return render_template('user/dashboard.html',
                           orders=orders, pharmacies=pharmacies,
                           receipts=receipts, notifications=notifications)


# ✅ NEW: Search medicine + pharmacy → full supply chain tracking
@app.route('/user/search', methods=['GET', 'POST'])
@login_required
@role_required(['user'])
def user_search():
    """Search by medicine name + optional pharmacy → shows complete tracking"""
    query = request.args.get('q', '').strip()
    pharmacy_id = request.args.get('pharmacy', '').strip()
    results = []

    if query:
        # Find medicine products matching the name (case-insensitive)
        meds = MedicineProduct.query.filter(
            MedicineProduct.name.ilike(f'%{query}%')
        ).all()

        for med in meds:
            # Find matching pharmacy request(s) where this medicine was dispatched
            reqs = PharmacyRequest.query.filter_by(medicine_id=med.id).order_by(PharmacyRequest.created_at.desc()).all()
            if pharmacy_id:
                reqs = [r for r in reqs if str(r.pharmacy_id) == str(pharmacy_id)]

            # Get blockchain events for full trace
            bc = get_blockchain()
            blocks = bc.blockchain.get_blocks_for_order(med.id) if hasattr(bc.blockchain, 'get_blocks_for_order') else []
            # Also collect events across all requests
            all_blocks = bc.get_all_blocks()
            order_events = []
            for r in reqs:
                for b in all_blocks:
                    if isinstance(b.get('event_data'), dict) and b['event_data'].get('request_id') == r.id:
                        order_events.append(b)

            results.append({
                'medicine': med,
                'requests': reqs,
                'events': order_events,
                'blockchain_verified': bc.verify()[0]
            })

    pharmacies = User.query.filter_by(role='pharmacy').all()
    return render_template('user/search.html',
                           query=query, pharmacy_id=pharmacy_id,
                           results=results, pharmacies=pharmacies)


@app.route('/user/place-order', methods=['POST'])
@login_required
@role_required(['user'])
def user_place_order():
    pharmacy_id = request.form.get('pharmacy_id')
    medicine_name = request.form.get('medicine_name')
    quantity = float(request.form.get('quantity'))
    unit = request.form.get('unit', 'strip')
    price = float(request.form.get('price'))

    order = UserOrder(
        user_id=current_user.id, pharmacy_id=pharmacy_id,
        medicine_name=medicine_name, quantity=quantity, unit=unit,
        price=price, status='pending', payment_status='pending'
    )
    db.session.add(order)
    db.session.commit()

    log_to_blockchain('USER_ORDER_PLACED', {
        'order_id': order.id, 'user': current_user.username,
        'pharmacy': User.query.get(pharmacy_id).username,
        'medicine': medicine_name, 'quantity': quantity, 'price': price,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(pharmacy_id, 'New Order from User',
                        f'{current_user.username} ordered {quantity} {unit} of {medicine_name}.',
                        'order', url_for('pharmacy_dashboard'))
    flash('Order placed!', 'success')
    return redirect(url_for('user_dashboard'))


@app.route('/user/pay-pharmacy/<int:order_id>')
@login_required
@role_required(['user'])
def user_pay_pharmacy(order_id):
    order = UserOrder.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        flash('Permission denied.', 'danger')
        return redirect(url_for('user_dashboard'))

    amount = order.price
    if current_user.wallet_balance < amount:
        flash('Insufficient wallet balance.', 'danger')
        return redirect(url_for('user_dashboard'))

    pharmacy = User.query.get(order.pharmacy_id)
    current_user.wallet_balance -= amount
    pharmacy.wallet_balance = (pharmacy.wallet_balance or 0) + amount

    receipt = create_receipt(current_user.id, pharmacy.id, amount,
                             'user_to_pharmacy', order.id, 'user_order',
                             f'Payment for {order.medicine_name}')

    order.payment_status = 'paid'
    order.receipt_number = receipt.receipt_number
    order.status = 'paid'

    log_to_blockchain('USER_PAID_PHARMACY', {
        'order_id': order.id, 'user': current_user.username,
        'pharmacy': pharmacy.username, 'amount': amount,
        'receipt': receipt.receipt_number,
        'timestamp': datetime.utcnow().isoformat()
    })

    create_notification(pharmacy.id, 'Payment Received',
                        f'{current_user.username} paid ${amount:.2f}.',
                        'payment', url_for('pharmacy_dashboard'))
    db.session.commit()
    flash(f'Paid! Receipt: {receipt.receipt_number}', 'success')
    return redirect(url_for('user_dashboard'))


@app.route('/user/receipts')
@login_required
@role_required(['user'])
def user_receipts():
    receipts = PaymentReceipt.query.filter_by(from_user_id=current_user.id).order_by(PaymentReceipt.created_at.desc()).all()
    return render_template('user/receipts.html', receipts=receipts)


# ==================== RECEIPT VIEW ====================

@app.route('/receipt/<receipt_number>')
@login_required
def view_receipt(receipt_number):
    receipt = PaymentReceipt.query.filter_by(receipt_number=receipt_number).first_or_404()
    if current_user.role != 'admin' and current_user.id not in [receipt.from_user_id, receipt.to_user_id]:
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('receipt.html', receipt=receipt)


# ==================== CHATBOT ====================

@app.route('/chatbot')
@login_required
@role_required(['user', 'pharmacy'])
def chatbot():
    return render_template('chatbot.html', languages=LANGUAGES)


@app.route('/api/chatbot', methods=['POST'])
@login_required
@role_required(['user', 'pharmacy'])
def chatbot_api():
    data = request.get_json() or {}
    user_message = (data.get('message') or '').strip()
    language = data.get('language', 'en')

    if not user_message:
        return jsonify({'error': 'Empty message'}), 400

    context_parts = []
    if current_user.role == 'user':
        orders = UserOrder.query.filter_by(user_id=current_user.id).limit(5).all()
        for o in orders:
            context_parts.append(f"Order #{o.id}: {o.medicine_name}, status={o.status}")
    elif current_user.role == 'pharmacy':
        reqs = PharmacyRequest.query.filter_by(pharmacy_id=current_user.id).limit(5).all()
        for r in reqs:
            context_parts.append(f"Request #{r.id}: {r.medicine_name}, status={r.status}")

    context_str = "\n".join(context_parts) if context_parts else "No recent activity."

    system_prompt = f"""You are MedAssist, an AI assistant for a Medicine Supply Chain system.
Help {current_user.role}s with medicine info, orders, health tips.
RULES: Always add "consult a doctor" disclaimer. Never diagnose.
User: {current_user.username} ({current_user.role})
Recent: {context_str}
Question: {user_message}
Answer in {LANGUAGES.get(language, {}).get('name', 'English')}."""

    try:
        if gemini_model is None:
            raise RuntimeError('Gemini not configured')
        chat = gemini_model.start_chat(history=[])
        response = chat.send_message(system_prompt)
        reply = response.text.strip() if response and response.text else "Sorry, no response."
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "I can help with medicine info and order tracking. Consult a doctor for medical advice."

    if language != 'en':
        reply = translate_text(reply, target_lang=language)

    try:
        audio_url = text_to_speech(reply, lang=language)
        cleanup_audio()
    except Exception:
        audio_url = None

    return jsonify({'reply': reply, 'audio_url': audio_url, 'language': language})


# ==================== BLOCKCHAIN ====================

@app.route('/blockchain')
@login_required
def blockchain_explorer():
    bc = get_blockchain()
    blocks = bc.get_all_blocks()
    is_valid, msg = bc.verify()
    return render_template('blockchain_explorer.html', blocks=blocks, is_valid=is_valid, verify_message=msg)


@app.route('/api/blockchain/verify')
@login_required
def api_blockchain_verify():
    bc = get_blockchain()
    ok, msg = bc.verify()
    return jsonify({'valid': ok, 'message': msg, 'total_blocks': len(bc.blockchain.chain)})


# ==================== CONTEXT PROCESSOR ====================

@app.context_processor
def utility_processor():
    return dict(
        User=User, MedicineProduct=MedicineProduct, MedicineCertificate=MedicineCertificate,
        PharmacyRequest=PharmacyRequest, UserOrder=UserOrder,
        MedicineReview=MedicineReview, Notification=Notification,
        PaymentReceipt=PaymentReceipt, LANGUAGES=LANGUAGES
    )


# ==================== INIT DB ====================

def create_tables():
    with app.app_context():
        db.create_all()

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@med.com', role='admin',
                         wallet_balance=1000000.0, is_approved=True)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()

        # Demo users with VALID licenses
        demo = [
            ('manufacturer1', 'mfg1@med.com', 'manufacturer', 'mfg123', 'LIC-MFG-001', 'PharmaCorp Ltd'),
            ('manufacturer2', 'mfg2@med.com', 'manufacturer', 'mfg123', 'LIC-MFG-002', 'MediLife Pharma'),
            ('supplier1', 'sup1@med.com', 'supplier', 'sup123', 'LIC-SUP-001', 'DistribuMed Inc'),
            ('supplier2', 'sup2@med.com', 'supplier', 'sup123', 'LIC-SUP-002', 'FastMeds Supply'),
            ('pharmacy1', 'ph1@med.com', 'pharmacy', 'ph123', 'LIC-PHR-001', 'City Pharmacy'),
            ('pharmacy2', 'ph2@med.com', 'pharmacy', 'ph123', 'LIC-PHR-002', 'HealthPlus Pharmacy'),
            ('user1', 'user1@med.com', 'user', 'user123', None, None),
            ('user2', 'user2@med.com', 'user', 'user123', None, None),
        ]
        for u, e, r, p, lic, comp in demo:
            if not User.query.filter_by(username=u).first():
                user = User(username=u, email=e, role=r, license_number=lic,
                            company_name=comp, wallet_balance=100000.0, is_approved=True)
                user.set_password(p)
                db.session.add(user)
        db.session.commit()

        # Demo medicines with uses/side-effects
        mfg = User.query.filter_by(username='manufacturer1').first()
        if mfg and MedicineProduct.query.count() == 0:
            cert = MedicineCertificate(
                manufacturer_id=mfg.id, product_name='Paracetamol 500mg',
                batch_number='B2024001', quality_grade='A',
                test_results='Purity: 99.8% | Passed all tests',
                expiry_date=datetime(2026, 12, 31),
                status='approved', approved_by=1, approved_at=datetime.utcnow()
            )
            db.session.add(cert)
            db.session.commit()

            samples = [
                ('Paracetamol 500mg', 'Paracetamol', 'Tablet', 'B2024001', 500, 'strip', 5.50,
                 'Fever and pain relief', False,
                 'Relieves fever, headache, body ache, mild pain',
                 'Nausea, stomach upset, rare allergic reaction',
                 '1-2 tablets every 4-6 hours after food. Max 8 tablets/day',
                 'Store below 25°C in dry place, away from sunlight'),
                ('Amoxicillin 250mg', 'Amoxicillin', 'Capsule', 'B2024002', 300, 'strip', 12.00,
                 'Antibiotic for bacterial infections', True,
                 'Respiratory, urinary, skin infections, dental abscess',
                 'Diarrhea, nausea, rash, rare allergic reaction',
                 '1 capsule 3 times a day for 5-7 days as prescribed',
                 'Store below 25°C, protect from moisture'),
                ('Cough Syrup 100ml', 'Dextromethorphan', 'Syrup', 'B2024003', 200, 'bottle', 8.50,
                 'Cough relief', False,
                 'Relieves dry cough, soothes throat irritation',
                 'Drowsiness, dizziness, mild nausea',
                 '10ml 3 times a day for adults. Not for children under 6',
                 'Store below 25°C, keep bottle tightly closed'),
                ('Insulin Injection', 'Human Insulin', 'Injection', 'B2024004', 100, 'vial', 35.00,
                 'Diabetes management', True,
                 'Controls blood sugar in Type 1 and Type 2 diabetes',
                 'Hypoglycemia, weight gain, injection site reactions',
                 'As prescribed by physician. Usually 0.5-1 unit/kg/day',
                 'Refrigerate at 2-8°C. Do not freeze'),
            ]
            for (name, comp, cat, batch, qty, unit, price, desc, presc, uses, se, dos, st) in samples:
                p = MedicineProduct(
                    manufacturer_id=mfg.id, name=name, composition=comp, category=cat,
                    batch_number=batch, mfg_date=datetime(2024, 1, 1),
                    expiry_date=datetime(2026, 12, 31),
                    quantity=qty, unit=unit, price_per_unit=price,
                    description=desc, prescription_required=presc,
                    uses=uses, side_effects=se, dosage=dos, storage=st,
                    status='available', certificate_id=cert.id,
                    rating=4.5, total_ratings=1, rating_sum=4.5
                )
                db.session.add(p)
            db.session.commit()


if __name__ == '__main__':
    create_tables()
    app.run(debug=True)