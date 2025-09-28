#!/usr/bin/env python3
"""
Test script to verify admin credit functionality works correctly
"""
import json
import os
import shutil
from main import add_credits, get_credits, load_user_credits, save_user_credits, user_credits

def test_credit_operations():
    """Test credit operations and persistence"""
    print("🧪 Testing Admin Credit System...")
    
    # Backup original credits file
    if os.path.exists("user_credits.json"):
        shutil.copy("user_credits.json", "user_credits_backup.json")
        print("✅ Backed up original credits file")
    
    # Test 1: Check current state
    print(f"\n📊 Current credits for user 6791428649: {get_credits(6791428649)}")
    
    # Test 2: Add credits
    print("\n💳 Testing add_credits function...")
    original_credits = get_credits(6791428649)
    add_credits(6791428649, 5)
    new_credits = get_credits(6791428649)
    
    print(f"Original: {original_credits}, After adding 5: {new_credits}")
    
    if new_credits == original_credits + 5:
        print("✅ add_credits function works correctly")
    else:
        print("❌ add_credits function failed!")
        return False
    
    # Test 3: Verify file was saved
    print("\n💾 Testing file persistence...")
    try:
        with open("user_credits.json", 'r') as f:
            file_data = json.load(f)
        
        if str(6791428649) in file_data and file_data[str(6791428649)] == new_credits:
            print("✅ Credits saved to file correctly")
        else:
            print("❌ Credits not saved to file properly!")
            print(f"File contents: {file_data}")
            return False
    except Exception as e:
        print(f"❌ Error reading credits file: {e}")
        return False
    
    # Test 4: Test reload from file
    print("\n🔄 Testing reload from file...")
    
    # Simulate restart by clearing memory and reloading
    user_credits.clear()
    reloaded_credits = load_user_credits()
    user_credits.update(reloaded_credits)
    
    reloaded_amount = get_credits(6791428649)
    
    if reloaded_amount == new_credits:
        print("✅ Credits persist correctly after reload")
    else:
        print(f"❌ Credits lost after reload! Expected: {new_credits}, Got: {reloaded_amount}")
        return False
    
    # Test 5: Test invalid user ID
    print("\n🚫 Testing invalid operations...")
    try:
        add_credits(0, 10)  # Should fail gracefully
        print("✅ Invalid user ID handled correctly")
    except Exception as e:
        print(f"❌ Invalid user ID caused error: {e}")
        return False
    
    print("\n🎉 All tests passed! Admin credit system is working correctly.")
    return True

if __name__ == "__main__":
    success = test_credit_operations()
    
    # Restore backup if exists
    if os.path.exists("user_credits_backup.json"):
        shutil.move("user_credits_backup.json", "user_credits.json")
        print("\n📥 Restored original credits file")
    
    if success:
        print("\n✅ Admin credit system is WORKING CORRECTLY")
        print("The /give_credits command should work properly")
    else:
        print("\n❌ Admin credit system has ISSUES that need fixing")