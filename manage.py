import argparse
import getpass
from autoprint.store import db, init, secret_hash, audit

def main():
    parser=argparse.ArgumentParser(description="Provision a local AutoPrint staff account")
    parser.add_argument("email")
    parser.add_argument("--role",choices=["owner","admin"],default="owner")
    args=parser.parse_args()
    email=args.email.strip().lower()
    if "@" not in email or len(email)>254:
        parser.error("Enter a valid staff email")
    password=getpass.getpass("New password (12 or more characters): ")
    if len(password)<12 or password!=getpass.getpass("Confirm password: "):
        parser.error("Use matching passwords of at least 12 characters")
    init()
    with db(True) as c:
        c.execute("INSERT INTO users(email,hash,role) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET hash=excluded.hash,role=excluded.role",
                  (email,secret_hash(password),args.role))
        c.execute("DELETE FROM logins WHERE uid=(SELECT id FROM users WHERE email=?)",(email,))
        audit(c,"local operator","Provisioned staff account",email)
    print("Staff account ready.")

if __name__=="__main__":
    main()
